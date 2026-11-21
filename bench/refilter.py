"""Offline check of the post-model code (quote filter + repairs): cached plans in, scores out. No model.

Cached plans were saved after the quote filter of their day; running today's `filter_plan` over them
shows what new rules drop or repair (it cannot bring back what the old filter dropped).

    uv run python -m bench.refilter coi bench/runs/opt-base [--out <dir>]   # 9 COI forms vs gold/plans
    uv run python -m bench.refilter dir bench/runs/drive-baseline [--out <dir>]  # run_dir output, notes only
    uv run python -m bench.refilter gold                                    # gold plans must pass untouched

`coi` prints per file gold=, conditions_precision and required_agreement before -> after and the notes;
`--out` also writes the new plan and COI JSON there. Paper texts are cached in bench/runs/_texts.
"""

import argparse
import json
import sys
from pathlib import Path

from app.builder import build
from app.extract import extract
from app.plan import Plan
from app.quotes import filter_plan, threshold_for
from app.rules import check
from bench.run_all import GOLD, ROOT, coi_files
from bench.score import score_plan

TEXTS = ROOT / "bench" / "runs" / "_texts"
KEYS = ("score", "conditions_precision", "required_agreement", "fields_f1")


def paper_text(form: str, path: Path) -> tuple[str, str]:
    """(text, extraction method) of one COI form file, cached on disk."""
    ext = path.suffix.lower().lstrip(".")
    cache = TEXTS / f"{form}.{ext}.json"
    if cache.exists():
        d = json.loads(cache.read_text())
        return d["text"], d["method"]
    doc = extract(path.read_bytes(), path.name)
    TEXTS.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"text": doc.text, "method": doc.method}, ensure_ascii=False))
    return doc.text, doc.method


def n_conditions(plan: Plan) -> int:
    return sum(t.show_if is not None for p in plan.pages for s in p.sections for t in [s, *s.fields])


def save(out: Path | None, name: str, plan: Plan) -> list[str]:
    coi = build(plan)
    if out:
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{name}.plan.json").write_text(plan.model_dump_json(indent=1))
        (out / f"{name}.coi.json").write_text(json.dumps(coi, ensure_ascii=False, indent=1))
    return check(coi)


def run_coi(run: Path, out: Path | None, quiet: bool) -> int:
    rows = []
    for form, path in coi_files(None, None):
        ext = path.suffix.lower().lstrip(".")
        plan_path = run / "coi" / form / f"{ext}.plan.json"
        if not plan_path.exists():
            continue
        text, method = paper_text(form, path)
        old = Plan.model_validate_json(plan_path.read_text())
        new, notes = filter_plan(old, text, threshold_for(method))
        gold = Plan.model_validate_json((GOLD / f"form{form}.json").read_text())
        a, b = score_plan(old, gold)["metrics"], score_plan(new, gold)["metrics"]
        problems = save(out, f"{form}.{ext}", new)
        rows.append((a, b))
        print(f"{form:>2} {ext:5} " + " ".join(f"{k}={a[k]:.3f}->{b[k]:.3f}" for k in KEYS)
              + f" conds={n_conditions(old)}->{n_conditions(new)} rules={len(problems)}")
        if not quiet:
            for n in notes:
                print("      " + n)
    if rows:
        print(f"MEAN n={len(rows)} " + " ".join(
            f"{k}={sum(a[k] for a, _ in rows) / len(rows):.3f}->{sum(b[k] for _, b in rows) / len(rows):.3f}" for k in KEYS))
    return 0


def run_dir(run: Path, out: Path | None, quiet: bool) -> int:
    for plan_path in sorted(run.glob("*.plan.json")):
        name = plan_path.name.removesuffix(".plan.json")
        text = (run / f"{name}.txt").read_text()
        old = Plan.model_validate_json(plan_path.read_text())
        new, notes = filter_plan(old, text)
        problems = save(out, name, new)
        print(f"{name[:48]:48} conds={n_conditions(old)}->{n_conditions(new)} notes={len(notes)} rules={len(problems)}")
        if not quiet:
            for n in notes:
                print("      " + n)
    return 0


def run_gold() -> int:
    """Every gold plan must pass today's filter with all its conditions and fields."""
    bad = 0
    for form, path in coi_files(None, {"pdf"}):
        text, method = paper_text(form, path)
        gold = Plan.model_validate_json((GOLD / f"form{form}.json").read_text())
        new, notes = filter_plan(gold, text, threshold_for(method))
        lost = n_conditions(gold) - n_conditions(new)
        bad += lost
        print(f"form {form}: conditions {n_conditions(gold)} -> {n_conditions(new)}, score {score_plan(new, gold)['metrics']['score']:.3f}")
        for n in notes:
            print("      " + n)
    print("gold conditions lost:", bad)
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["coi", "dir", "gold"])
    ap.add_argument("run", nargs="?")
    ap.add_argument("--out")
    ap.add_argument("--quiet", action="store_true", help="no notes")
    a = ap.parse_args()
    if a.mode == "gold":
        return run_gold()
    if not a.run:
        ap.error("run folder needed")
    return (run_coi if a.mode == "coi" else run_dir)(Path(a.run), Path(a.out) if a.out else None, a.quiet)


if __name__ == "__main__":
    sys.exit(main())
