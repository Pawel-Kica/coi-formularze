"""Simpler approaches against ours on the same documents: the numbers for the README and the deck (`bench/ARMS.md`).

Arms, simplest first (ours = `bench.run_all`):
- `file`    the whole file straight into one model (mistral/mistral-large-2512, Eden file input; JPG/PNG as an
            image) -> COI JSON in one call
- `images`  page images (as our reader gets them) -> Qwen3.5 -> COI JSON in one call
- `md`      Qwen3.5 Markdown (cached, the same reading as ours, `--md`) -> GLM-5.2 -> COI JSON in one call
- `nocode`  ours without the code after the model: a cached unfiltered plan (`--raw`) -> builder, no quote filter
            and no repairs (no model call)
- `read`    only our reading (Qwen3.5, page images -> Markdown): its seconds and $, which the `md` arm shares

The direct arms (`file`, `images`, `md`) share one prompt: `bench/arms_direct.md` + the COI task documents read
from `data/coi/` at run time (technical rules, JSON description, JSON schema, dictionaries, Standards chapter 2,
the form 1 reference JSON as the example). Only the form input differs.

    uv run python -m bench.arms --arm images --file "data/coi/3. .../Wniosek o wydanie zaświadczenia.pdf" \
        --out /Users/main/coi-formularze/bench/runs/loop-arms
    uv run python -m bench.arms --arm md --file <form file> --md <run>/coi/3/pdf.text.md --out ...
    uv run python -m bench.arms --arm nocode --file <form file> --raw <run>/<file>.raw.json --out ...

Writes `<out>/<arm>/<form>-<ext>.coi.json` (when the answer is a JSON object), `.raw.txt` (the model's answer) and
`.meta.json` (ok, error, seconds, $, tokens, COI rule problems, element counts) and prints one line. A direct answer
that is not a JSON object counts as failed; code fences or text around the object are cut off first.
"""

import argparse
import base64
import json
import re
import sys
import time
from functools import cache
from pathlib import Path

from app.builder import build
from app.config import settings
from app.extract import extract
from app.llm import client
from app.llm.transcribe import data_url, render_pages, transcribe
from app.plan import Plan
from app.rules import check
from bench.run_all import COI, PRICE_PER_M, priced

HERE = Path(__file__).resolve().parent
DOCS = COI / "Materiały do zadania dla Uczestników"
FILE_MODEL = "mistral/mistral-large-2512"
IMAGES_MODEL = "deepinfra/Qwen/Qwen3.5-397B-A17B"
MD_MODEL = "deepinfra/zai-org/GLM-5.2"
PRICE_PER_M.setdefault(FILE_MODEL, (0.50, 1.50))  # Eden catalog GET /v3/models 22.11
MAX_TOKENS = 60000  # form 8 in COI JSON is ~30k tokens
TIMEOUT_S = 1800
DIRECT = ("file", "images", "md")  # arms whose answer is the COI JSON itself
USER = "Zaprojektuj e-usługę z tego pustego formularza papierowego{how} i zwróć jej JSON."


@cache
def system_prompt() -> str:
    """`arms_direct.md` with the COI documents filled in (the same text for every direct arm)."""

    def doc(name: str) -> str:
        path = next(DOCS.glob(name))
        return extract(path.read_bytes(), path.name).text.strip()

    standards = doc("*Standardy*.pdf")
    standards = standards[standards.rfind("Projektowanie formularza"):]  # chapter 2; chapter 1 is tone of voice
    schema = json.loads(next(DOCS.glob("importowanyPlikJsonSchemat*.json")).read_text())
    example = json.loads(next(next(COI.glob("1.*")).glob("konfiguracja_form_*.json")).read_text())
    docs = "\n\n".join([
        "# Reguły techniczne konfiguracji formularzy i pól na formularzu\n\n" + doc("Reguły techniczne*.docx"),
        "# Opis importowanego pliku JSON\n\n" + doc("OpisImportowanegoPlikuJSON*.docx"),
        "# Schemat JSON importowanego pliku\n\n" + json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
        "# Słowniki\n\n" + doc("Hacknation - Słowniki*.docx"),
        "# Standardy i dobre praktyki projektowania formularzy (rozdział 2)\n\n" + standards,
        "# Przykład: JSON wzorcowy COI dla formularza „Wniosek o ustalenie numeru porządkowego nieruchomości”\n\n"
        + json.dumps(example, ensure_ascii=False, separators=(",", ":")),
    ])
    return (HERE / "arms_direct.md").read_text().replace("{docs}", docs)


def call(model: str, content: list[dict], think: bool = False) -> str:
    """One chat call with content parts (text, image_url, file), JSON object mode; logged in `client.CALLS`.
    Thinking off as in every Eden number (`client.model_params`); `think` = `reasoning_effort: "high"` and more room."""
    body = {"model": model, "temperature": 0, "max_tokens": MAX_TOKENS * (2 if think else 1),
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system_prompt()}, {"role": "user", "content": content}],
            **client.model_params(model, 42)} | ({"reasoning_effort": "high"} if think else {})
    t0 = time.monotonic()
    out = client._post(f"{settings.llm_base_url.rstrip('/')}/chat/completions", body, TIMEOUT_S, 2).json()
    choice = (out.get("choices") or [{}])[0]
    usage = out.get("usage") or {}
    client.CALLS.append({"model": model, "seconds": round(time.monotonic() - t0, 2),
                         "prompt_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens"),
                         "finish_reason": choice.get("finish_reason")})
    return (choice.get("message") or {}).get("content") or ""


def file_part(data: bytes, filename: str) -> dict:
    """The file as one content part: a PDF as an OpenAI `file` part (base64), an image as `image_url`."""
    if data[:5] == b"%PDF-":
        url = "data:application/pdf;base64," + base64.b64encode(data).decode("ascii")
        return {"type": "file", "file": {"file_data": url, "filename": Path(filename).name}}
    mime = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64," + base64.b64encode(data).decode("ascii")}}


def parse(raw: str) -> dict:
    """The JSON object in a model answer (code fences and text around it cut off). Raises ValueError."""
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in the answer")
    data = json.loads(raw[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("JSON is not an object")  # noqa: TRY004 - callers catch ValueError for any bad answer
    return data


def problem_kinds(problems: list[str]) -> dict:
    """COI rule problems by kind: `mechanical` (ids, techNames: a few lines of code fix them), `conditions`
    (jsonata queries), `structure` (everything else: nesting, options, pages, flow)."""
    kinds = {"mechanical": 0, "structure": 0, "conditions": 0}
    for p in problems:
        if re.search(r"techName|\bid\b|not unique", p):
            kinds["mechanical"] += 1
        elif "query" in p:
            kinds["conditions"] += 1
        else:
            kinds["structure"] += 1
    return kinds


def elements(coi: dict) -> dict:
    """Pages, fields per type and SHOW conditions of a COI JSON (nested repeating-section fields included)."""
    types, shows = {}, 0

    def walk(questions):
        nonlocal shows
        for q in questions or []:
            f = q.get("field") if isinstance(q, dict) else None
            f = f if isinstance(f, dict) else {}
            types[f.get("type", "?")] = types.get(f.get("type", "?"), 0) + 1
            shows += sum(1 for s in f.get("states") or [] if isinstance(s, dict) and s.get("type") == "SHOW")
            walk(f.get("repeatingSectionGrid"))

    tabs = coi.get("tabs") if isinstance(coi.get("tabs"), list) else []
    for t in tabs:
        walk(t.get("questions") if isinstance(t, dict) else None)
    inputs = sum(n for t, n in types.items() if t not in {"SECTION", "REPEATING_SECTION", "TEXT_BLOCK", "INFO_SECTION", "?"})
    return {"pages": len(tabs), "inputs": inputs, "show": shows, "types": dict(sorted(types.items()))}


def run(arm: str, path: Path, md: Path | None, raw_plan: Path | None, think: bool = False) -> tuple[str, dict | None, dict]:
    """One arm on one file -> (raw answer, COI JSON or None, extra meta)."""
    data = path.read_bytes()
    if arm == "file":
        parts = [file_part(data, path.name), {"type": "text", "text": USER.format(how=" (plik)")}]
        return call(FILE_MODEL, parts, think), None, {}
    if arm == "images":
        pages = render_pages(data, path.name)
        parts = [{"type": "image_url", "image_url": {"url": data_url(p, settings.transcribe_side)}} for p in pages]
        how = f" (obrazy stron, {len(pages)} str.)"
        parts.append({"type": "text", "text": USER.format(how=how)})
        return call(IMAGES_MODEL, parts, think), None, {"pages_in": len(pages)}
    if arm == "md":
        text = md.read_text()
        prompt = f"Tekst formularza (Markdown odczytany ze stron):\n```\n{text}\n```\n\n" + USER.format(how="")
        return call(MD_MODEL, [{"type": "text", "text": prompt}], think), None, {"md": str(md)}
    if arm == "nocode":
        coi = build(Plan.model_validate_json(raw_plan.read_text()))
        return "", coi, {"raw_plan": str(raw_plan)}
    if arm == "read":
        doc = transcribe(data, path.name)
        return doc.text, None, {"pages_in": len(doc.pages)}
    raise ValueError(f"unknown arm {arm}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=["file", "images", "md", "nocode", "read"])
    ap.add_argument("--file", required=True, type=Path, help="a form file under data/coi/<N>. .../")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--md", type=Path, help="md arm: cached Markdown of the file (<run>/coi/<form>/<ext>.text.md)")
    ap.add_argument("--raw", type=Path, help="nocode arm: cached unfiltered plan (<run>/<file>.raw.json)")
    ap.add_argument("--think", action="store_true", help="direct arms: thinking on (writes to <out>/<arm>-think)")
    a = ap.parse_args()
    if a.arm == "md" and not a.md or a.arm == "nocode" and not a.raw:
        ap.error(f"--arm {a.arm} needs --{'md' if a.arm == 'md' else 'raw'}")
    form, ext = a.file.parent.name.split(".")[0], a.file.suffix.lower().lstrip(".")
    out = a.out / (a.arm + ("-think" if a.think else ""))
    out.mkdir(parents=True, exist_ok=True)
    stem = out / f"{form}-{ext}"
    t, n0 = time.time(), len(client.CALLS)
    meta = {"arm": a.arm + ("-think" if a.think else ""), "file": a.file.name}
    try:
        raw, coi, extra = run(a.arm, a.file, a.md, a.raw, a.think)
        meta |= extra
    except Exception as e:  # noqa: BLE001 - a benchmark records every failure
        raw, coi, meta["error"] = "", None, f"{type(e).__name__}: {e}"[:500]
    meta |= {"s": round(time.time() - t)} | priced(client.CALLS[n0:])
    meta["finish"] = [c.get("finish_reason") for c in client.CALLS[n0:]]
    if raw:
        Path(f"{stem}.raw.txt").write_text(raw)
    if a.arm in DIRECT and "error" not in meta:
        try:
            coi = parse(raw)
        except ValueError as e:
            meta["error"] = f"not valid JSON: {e}"[:300]
    if coi is not None:
        Path(f"{stem}.coi.json").write_text(json.dumps(coi, ensure_ascii=False, indent=1))
        try:
            problems = check(coi)
        except Exception as e:  # noqa: BLE001 - a broken shape can crash the checker; that is a problem too
            problems = [f"check crashed: {type(e).__name__}: {e}"[:300]]
        meta |= {"problems": problems, "kinds": problem_kinds(problems), "elements": elements(coi)}
    meta["ok"] = "error" not in meta and (coi is not None or a.arm == "read")
    Path(f"{stem}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    summary = (f"rules={len(meta['problems'])} {meta['elements']}" if "problems" in meta else meta.get("error", ""))
    print(f"{'OK ' if meta['ok'] else 'ERR'} {a.arm:7} {form:>2} {ext:4} {meta['s']:4}s ${meta['usd']:.4f} "
          f"in={meta['tokens_in']} out={meta['tokens_out']} {summary}"[:600], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
