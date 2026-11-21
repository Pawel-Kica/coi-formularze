"""End-to-end run of the real pipeline over form files: the definition-of-done table.

For every file: extract -> make_plan -> filter_plan -> build -> check. Saves
`<out>/<set>/<form>/<ext>.plan.json` and `.coi.json`, then prints per file:
JSON produced (1), COI rules problems (2), quote-filter drops (hallucination
proxy), score vs the gold plan, for forms 1 and 5 score vs the COI reference (3),
and per form the consistency of plans across formats vs the PDF plan (4).

    uv run python bench/run_all.py                       # 9 COI forms, all formats
    uv run python bench/run_all.py --forms 1,5 --formats pdf
    uv run python bench/run_all.py --bip                 # gmina BIP black-box set
    uv run python bench/run_all.py --out bench/runs/e2e  # reuse: files already done are skipped
"""

import argparse
import json
import sys
import time
from pathlib import Path

from app.builder import build
from app.extract import extract
from app.llm.planner import make_plan
from app.plan import Plan
from app.quotes import filter_plan
from app.rules import check
from bench.score import score_coi, score_plan

ROOT = Path(__file__).resolve().parent.parent
COI = ROOT / "data" / "coi"
BIP = Path.home() / "hacknation-2026" / "resources" / "bip-formularze"
GOLD = ROOT / "gold" / "plans"
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
    d = next((p for p in COI.iterdir() if p.name.startswith(f"{form}.")), None)
    ref = next(d.glob("konfiguracja_form_*.json"), None) if d else None
    return json.loads(ref.read_text()) if ref else None


def run_file(form: str, path: Path, out: Path) -> dict:
    ext = path.suffix.lower().lstrip(".")
    plan_path, coi_path = out / form / f"{ext}.plan.json", out / form / f"{ext}.coi.json"
    row = {"form": form, "ext": ext, "file": path.name}
    if coi_path.exists() and plan_path.exists():
        plan, coi = Plan.model_validate_json(plan_path.read_text()), json.loads(coi_path.read_text())
        row["cached"] = True
    else:
        t = time.time()
        try:
            doc = extract(path.read_bytes(), path.name)
            plan, dropped = filter_plan(make_plan(doc.text), doc.text)
            coi = build(plan)
        except Exception as e:  # noqa: BLE001 - a benchmark records every failure
            return row | {"ok": False, "error": f"{type(e).__name__}: {e}"[:300], "s": round(time.time() - t)}
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(plan.model_dump_json(indent=1))
        coi_path.write_text(json.dumps(coi, ensure_ascii=False, indent=1))
        row |= {"dropped": dropped, "s": round(time.time() - t)}
    row |= {"ok": True, "problems": check(coi), "fields": sum(len(s.fields) for p in plan.pages for s in p.sections)}
    gold = GOLD / f"form{form}.json"
    if gold.exists():
        row["gold"] = round(score_plan(plan, Plan.model_validate_json(gold.read_text()))["metrics"]["score"], 3)
    ref = reference(form)
    if ref:
        row["ref"] = round(score_coi(coi, ref)["recall"]["overall"], 3)
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
    ap.add_argument("--out", default=f"bench/runs/e2e-{time.strftime('%Y%m%d-%H%M')}")
    a = ap.parse_args()
    out = Path(a.out) / ("bip" if a.bip else "coi")
    files = bip_files() if a.bip else coi_files(set(a.forms.split(",")) if a.forms else None,
                                                set(a.formats.split(",")) if a.formats else None)
    rows = []
    for form, path in files:
        r = run_file(form, path, out)
        rows.append(r)
        status = "OK " if r.get("ok") and not r.get("problems") else "ERR"
        extra = (f"rules={len(r.get('problems', []))} drop={len(r.get('dropped', []))} fields={r.get('fields')}"
                 f" gold={r.get('gold', '-')} ref={r.get('ref', '-')}" if r.get("ok") else r["error"])
        print(f"{status} {form:>3} {r['ext']:5} {r.get('s', 0):4}s {extra}", flush=True)
    cons = consistency(rows, out)
    summary = {"files": len(rows), "json_ok": sum(bool(r.get("ok")) for r in rows),
               "rules_ok": sum(bool(r.get("ok")) and not r["problems"] for r in rows), "consistency": cons}
    (out / "summary.json").write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1))
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
