"""Run the real pipeline over every file in a folder, for black-box sets without gold.

For every file: extract -> make_plan -> filter_plan -> build -> check, 2 files at a time
(Ollama has 2 slots). Saves `<out>/<file name>.txt` (extracted text), `.plan.json`,
`.coi.json`, and `<out>/summary.json` (per file: ok/error, seconds, fields, rules problems,
quote-filter drops). A Claude judge then compares each original with its output.

    uv run python -m bench.run_dir ~/hacknation-2026/resources/drive-formularze --out bench/runs/drive-baseline
    uv run python -m bench.run_dir <folder> --out <earlier run>   # files already done are skipped
"""

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.builder import build
from app.extract import extract
from app.llm.planner import make_plan
from app.quotes import THRESHOLD, THRESHOLD_OCR, filter_plan
from app.rules import check

SKIP_NAMES = {"README.md", "source.md", ".DS_Store"}


def run_file(path: Path, out: Path) -> dict:
    row = {"file": path.name}
    coi_path = out / f"{path.name}.coi.json"
    if coi_path.exists():
        return row | {"ok": True, "cached": True}
    t = time.time()
    try:
        doc = extract(path.read_bytes(), path.name)
        (out / f"{path.name}.txt").write_text(doc.text)
        threshold = THRESHOLD_OCR if "ocr" in doc.method else THRESHOLD
        plan, dropped = filter_plan(make_plan(doc.text), doc.text, threshold)
        coi = build(plan)
    except Exception as e:  # noqa: BLE001 - a benchmark records every failure
        return row | {"ok": False, "error": f"{type(e).__name__}: {e}"[:300], "s": round(time.time() - t)}
    (out / f"{path.name}.plan.json").write_text(plan.model_dump_json(indent=1))
    coi_path.write_text(json.dumps(coi, ensure_ascii=False, indent=1))
    return row | {"ok": True, "s": round(time.time() - t), "method": doc.method, "dropped": dropped,
                  "problems": check(coi), "fields": sum(len(s.fields) for p in plan.pages for s in p.sections)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--out", default=f"bench/runs/dir-{time.strftime('%Y%m%d-%H%M')}")
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    files = sorted(f for f in Path(a.folder).expanduser().iterdir() if f.is_file() and f.name not in SKIP_NAMES)

    def one(path: Path) -> dict:
        r = run_file(path, out)
        status = "OK " if r.get("ok") and not r.get("problems") else "ERR"
        extra = (f"rules={len(r.get('problems', []))} drop={len(r.get('dropped', []))} fields={r.get('fields')}"
                 if r.get("ok") else r["error"])
        print(f"{status} {r.get('s', 0):4}s {path.name}  {extra}", flush=True)
        return r

    with ThreadPoolExecutor(a.workers) as pool:
        rows = list(pool.map(one, files))
    summary = {"files": len(rows), "json_ok": sum(bool(r.get("ok")) for r in rows),
               "rules_ok": sum(bool(r.get("ok")) and not r.get("problems") for r in rows)}
    (out / "summary.json").write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1))
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
