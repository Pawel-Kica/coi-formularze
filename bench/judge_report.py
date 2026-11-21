"""Judge verdicts -> `<run_dir>/judge/REPORT.md`, worst first; also the checked way to add a verdict.

    uv run python -I bench/judge_report.py bench/runs/drive-baseline          # write REPORT.md
    uv run python -I bench/judge_report.py bench/runs/drive-baseline --add <<'EOF'
    {"file": "04_x.doc", "verdict": "źle", "category": "brak pól", "reasons": ["..."], "paper_elements": 20, "covered": 14, "basis": "obrazy"}
    EOF

`--add` validates one verdict (format in `bench/JUDGE.md`) and appends it as one line to
`<run_dir>/judge/verdicts.jsonl`; a file judged again keeps its last line. Seconds and field
counts come from `summary.json` (or the saved plan while the run is still going).
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bench.judge_kit import plan_fields, summary_rows

VERDICTS = ("dobrze", "źle")
CATEGORIES = ("wejście", "brak pól", "złe typy/opcje", "warunki", "zmyślone", "reguły COI")
BASES = ("obrazy", "original.txt", "extracted.txt")  # what the judge compared against


def validate(v: dict, judge_dir: Path) -> list[str]:
    errs = []
    if not (judge_dir / str(v.get("file"))).is_dir():
        errs.append(f"file: no kit at {judge_dir / str(v.get('file'))}")
    if v.get("verdict") not in VERDICTS:
        errs.append(f"verdict: one of {VERDICTS}")
    if v.get("category") not in CATEGORIES and not (v.get("verdict") == "dobrze" and v.get("category") is None):
        errs.append(f"category: one of {CATEGORIES} (null only with dobrze)")
    reasons = v.get("reasons")
    if not isinstance(reasons, list) or len(reasons) > 3 or not all(isinstance(r, str) for r in reasons):
        errs.append("reasons: list of at most 3 strings")
    elif v.get("verdict") == "źle" and not reasons:
        errs.append("reasons: at least one for źle")
    if v.get("basis") not in BASES:
        errs.append(f"basis: one of {BASES}")
    for k in ("paper_elements", "covered"):
        if not isinstance(v.get(k), int) or v[k] < 0:
            errs.append(f"{k}: integer >= 0")
    if not errs and v["covered"] > v["paper_elements"]:
        errs.append("covered: at most paper_elements")
    return errs


def add(run_dir: Path, line: str) -> int:
    judge = run_dir / "judge"
    try:
        v = json.loads(line)
    except json.JSONDecodeError as e:
        print(f"not JSON: {e}", file=sys.stderr)
        return 1
    if errs := validate(v, judge):
        print("rejected:\n  " + "\n  ".join(errs), file=sys.stderr)
        return 1
    with open(judge / "verdicts.jsonl", "a") as f:  # one write per line: parallel judges don't interleave
        f.write(json.dumps(v, ensure_ascii=False) + "\n")
    print(f"ok {v['file']}: {v['verdict']}")
    return 0


def _cell(text: str) -> str:
    return str(text).replace("|", "/").replace("\n", " ")


def report(run_dir: Path) -> str:
    judge = run_dir / "judge"
    verdicts = {}
    vpath = judge / "verdicts.jsonl"
    for line in vpath.read_text().splitlines() if vpath.exists() else []:
        if line.strip():
            v = json.loads(line)
            verdicts[v["file"]] = v
    rows = summary_rows(run_dir)
    files = sorted(p.name for p in judge.iterdir() if p.is_dir())

    def key(name):  # źle first, then lowest coverage; files without a verdict last
        v = verdicts.get(name)
        if not v:
            return (2, 0.0, name)
        cov = v["covered"] / v["paper_elements"] if v["paper_elements"] else 0.0
        return (0 if v["verdict"] == "źle" else 1, cov, name)

    lines = ["| plik | werdykt | kategoria | powody | pokrycie | podstawa | s | pola |", "|---|---|---|---|---|---|---|---|"]
    for name in sorted(files, key=key):
        v, r = verdicts.get(name, {}), rows.get(name, {})
        fields = r.get("fields", plan_fields(run_dir, name))
        cov = f"{v['covered']}/{v['paper_elements']}" if v else ""
        lines.append(f"| {_cell(name)} | {v.get('verdict', 'brak werdyktu')} | {v.get('category') or ''} | "
                     f"{_cell('; '.join(v.get('reasons', [])))} | {cov} | {v.get('basis', '')} | {r.get('s', '')} | "
                     f"{'' if fields is None else fields} |")

    judged = [verdicts[f] for f in files if f in verdicts]
    by_verdict = Counter(v["verdict"] for v in judged)
    by_cat = Counter(v["category"] for v in judged if v["verdict"] == "źle")
    head = [f"# Werdykty sędziego: {run_dir.name}", "",
            f"- Plików z kitem: {len(files)}, z werdyktem: {len(judged)}, bez werdyktu: {len(files) - len(judged)}",
            f"- dobrze: {by_verdict['dobrze']}, źle: {by_verdict['źle']}",
            "- źle wg kategorii: " + (", ".join(f"{c} {n}" for c, n in by_cat.most_common()) or "brak"),
            "", "Pokrycie = elementy papieru obecne u nas / wszystkie elementy papieru (liczy sędzia). "
            "Podstawa = z czym sędzia porównywał (obrazy, original.txt z textutil, albo tylko nasz extracted.txt).", ""]
    return "\n".join(head + lines + [""])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--add", action="store_true", help="read one verdict JSON from stdin and append it")
    a = ap.parse_args()
    run_dir = Path(a.run_dir).expanduser()
    if a.add:
        return add(run_dir, sys.stdin.read().strip())
    out = run_dir / "judge" / "REPORT.md"
    out.write_text(report(run_dir))
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
