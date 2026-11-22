"""End-to-end run of the real pipeline over form files: the definition-of-done table.

For every file: transcribe (page images -> Markdown) -> make_plan -> filter_plan -> build -> check. Saves
`<out>/<set>/<form>/<ext>.text.md` (what the planner read), `.plan.json` and `.coi.json`, then prints per file:
JSON produced (1), COI rules problems (2), quote-filter drops (hallucination
proxy), score vs the gold plan, for forms 1 and 5 score vs the COI reference (3)
plus `walid=` (validator F1 vs gold/coi/formN.json, the reference with hand-added
validation; `(+N unscored)` = its validators on fields `score_coi` did not match, not in
the F1), and per form the consistency of plans across formats vs the PDF plan (4).

    uv run python -m bench.run_all                       # 9 COI forms, all formats
    uv run python -m bench.run_all --forms 1,5 --formats pdf
    uv run python -m bench.run_all --bip                 # gmina BIP black-box set
    uv run python -m bench.run_all --out bench/runs/e2e  # reuse: files already done are skipped
    uv run python -m bench.run_all --out bench/runs/e2e --rebuild  # cached plans, fresh build (no LLM)
"""

import argparse
import json
import sys
import time
from pathlib import Path

from app.builder import build
from app.config import settings
from app.llm import client
from app.llm.planner import make_plan
from app.llm.transcribe import transcribe
from app.plan import Plan
from app.quotes import filter_plan
from app.rules import check
from bench.score import score_coi, score_plan

ROOT = Path(__file__).resolve().parent.parent
COI = ROOT / "data" / "coi"
BIP = Path.home() / "hacknation-2026" / "resources" / "bip-formularze"
GOLD = ROOT / "gold" / "plans"
GOLD_COI = ROOT / "gold" / "coi"  # COI references + hand-added validation (same structure)
SKIP = ("konfiguracja_form", "wizualizacja")


def coi_files(forms: set[str] | None, formats: set[str] | None) -> list[tuple[str, Path]]:
    out = []
    for d in sorted(p for p in COI.iterdir() if p.is_dir() and p.name[0].isdigit()):
        form = d.name.split(".")[0]
        if forms and form not in forms:
            continue
        for f in sorted(d.iterdir()):
            ext = f.suffix.lower().lstrip(".")
            if any(s in f.name for s in SKIP) or ext == "json" or (formats and ext not in formats):
                continue
            out.append((form, f))
    return out


def bip_files() -> list[tuple[str, Path]]:
    return [(d.name.split("-")[0], f) for d in sorted(p for p in BIP.iterdir() if p.is_dir())
            for f in sorted(d.iterdir()) if f.name != "source.md"]


def reference(form: str) -> dict | None:
    """The gold copy if there is one (same structure, so `ref=` is unchanged), else the COI original."""
    gold = GOLD_COI / f"form{form}.json"
    if gold.exists():
        return json.loads(gold.read_text())
    d = next((p for p in COI.iterdir() if p.name.startswith(f"{form}.")), None)
    ref = next(d.glob("konfiguracja_form_*.json"), None) if d else None
    return json.loads(ref.read_text()) if ref else None


def setup() -> dict:
    """Models and page size, for bench logs (tells runs apart)."""
    return {"model": settings.llm_model, "transcribe_model": settings.transcribe_model,
            "transcribe_side": settings.transcribe_side}


# $ per 1M tokens (input, output), Eden catalog GET /v3/models 22.11; unknown models are priced 0
PRICE_PER_M = {"deepinfra/zai-org/GLM-5.2": (0.75, 2.40), "deepinfra/Qwen/Qwen3.5-397B-A17B": (0.45, 3.00)}


def priced(calls: list[dict]) -> dict:
    """Tokens and $ of one file's model calls (a slice of `client.CALLS`)."""
    cost = sum((c.get("prompt_tokens") or 0) * PRICE_PER_M.get(c["model"], (0, 0))[0]
               + (c.get("output_tokens") or 0) * PRICE_PER_M.get(c["model"], (0, 0))[1] for c in calls) / 1e6
    return {"usd": round(cost, 4), "calls": len(calls),
            "tokens_in": sum(c.get("prompt_tokens") or 0 for c in calls),
            "tokens_out": sum(c.get("output_tokens") or 0 for c in calls)}


def run_file(form: str, path: Path, out: Path, rebuild: bool = False) -> dict:
    """One file through the pipeline, or from its cached plan/COI. `<ext>.meta.json` keeps the
    quote drops and seconds of the model run, so a cached row still has them."""
    ext = path.suffix.lower().lstrip(".")
    plan_path, coi_path = out / form / f"{ext}.plan.json", out / form / f"{ext}.coi.json"
    meta_path = out / form / f"{ext}.meta.json"
    row = {"form": form, "ext": ext, "file": path.name}
    if rebuild and not plan_path.exists():  # --rebuild never calls the model
        return row | {"ok": False, "error": "not cached (--rebuild)"}
    if plan_path.exists() and (rebuild or coi_path.exists()):
        plan = Plan.model_validate_json(plan_path.read_text())
        row["cached"] = True
        if meta_path.exists():
            row |= json.loads(meta_path.read_text())
        if rebuild:  # builder changed: rebuild the COI JSON from the cached plan
            coi = build(plan)
            coi_path.write_text(json.dumps(coi, ensure_ascii=False, indent=1))
        else:
            coi = json.loads(coi_path.read_text())
    else:
        t, n0 = time.time(), len(client.CALLS)
        try:
            data = path.read_bytes()
            doc = transcribe(data, path.name)
            (out / form).mkdir(parents=True, exist_ok=True)
            (out / form / f"{ext}.text.md").write_text(doc.text)  # what the planner read
            plan, dropped = filter_plan(make_plan(doc.text), doc.text)
            coi = build(plan)
        except Exception as e:  # noqa: BLE001 - a benchmark records every failure
            return row | {"ok": False, "error": f"{type(e).__name__}: {e}"[:300], "s": round(time.time() - t)}
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(plan.model_dump_json(indent=1))
        coi_path.write_text(json.dumps(coi, ensure_ascii=False, indent=1))
        meta = {"dropped": dropped, "s": round(time.time() - t)} | priced(client.CALLS[n0:])
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
        row |= meta
    row |= {"ok": True, "problems": check(coi), "fields": sum(len(s.fields) for p in plan.pages for s in p.sections)}
    gold = GOLD / f"form{form}.json"
    if gold.exists():
        row["gold"] = round(score_plan(plan, Plan.model_validate_json(gold.read_text()))["metrics"]["score"], 3)
    ref = reference(form)
    if ref:
        rep = score_coi(coi, ref)
        row["ref"] = round(rep["recall"]["overall"], 3)
        if (GOLD_COI / f"form{form}.json").exists():
            row["walid"] = rep["validation"]["f1"]
            row["walid_missing"], row["walid_extra"] = rep["validation"]["missing"], rep["validation"]["extra"]
            row["walid_unmatched"] = rep["validation"]["unmatched"]  # gold validators on unmatched fields, not in F1
    return row


def consistency(rows: list[dict], out: Path) -> dict[str, float]:
    """Per form: mean score_plan of each non-PDF plan against the PDF plan (1.0 = same plan)."""
    res = {}
    for form in sorted({r["form"] for r in rows}):
        base = out / form / "pdf.plan.json"
        others = [r for r in rows if r["form"] == form and r.get("ok") and r["ext"] != "pdf"]
        if not base.exists() or not others:
            continue
        pdf_plan = Plan.model_validate_json(base.read_text())
        scores = [score_plan(Plan.model_validate_json((out / form / f"{r['ext']}.plan.json").read_text()),
                             pdf_plan)["metrics"]["score"] for r in others]
        res[form] = round(sum(scores) / len(scores), 3)
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--forms", help="comma list, e.g. 1,5")
    ap.add_argument("--formats", help="comma list, e.g. pdf,docx,png")
    ap.add_argument("--bip", action="store_true", help="run the BIP black-box set instead")
    ap.add_argument("--rebuild", action="store_true", help="rebuild COI JSON from cached plans")
    ap.add_argument("--out", default=f"bench/runs/e2e-{time.strftime('%Y%m%d-%H%M')}")
    a = ap.parse_args()
    if a.rebuild and not Path(a.out).exists():
        ap.error("--rebuild needs --out of an earlier run")
    out = Path(a.out) / ("bip" if a.bip else "coi")
    files = bip_files() if a.bip else coi_files(set(a.forms.split(",")) if a.forms else None,
                                                set(a.formats.split(",")) if a.formats else None)
    llm = setup()
    print("llm:", json.dumps(llm), flush=True)
    rows = []
    for form, path in files:
        r = run_file(form, path, out, a.rebuild)
        rows.append(r)
        status = "OK " if r.get("ok") and not r.get("problems") else "ERR"
        extra = (f"rules={len(r.get('problems', []))} drop={len(r.get('dropped', []))} fields={r.get('fields')}"
                 f" gold={r.get('gold', '-')} ref={r.get('ref', '-')}"
                 + (f" walid={r['walid']:.2f} (+{r['walid_unmatched']} unscored)" if "walid" in r else "") if r.get("ok") else r["error"])
        print(f"{status} {form:>3} {r['ext']:5} {r.get('s', 0):4}s {extra}", flush=True)
    cons = consistency(rows, out)
    summary = {"files": len(rows), "json_ok": sum(bool(r.get("ok")) for r in rows),
               "rules_ok": sum(bool(r.get("ok")) and not r["problems"] for r in rows), "consistency": cons,
               "llm": llm}
    (out / "summary.json").write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1))
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
