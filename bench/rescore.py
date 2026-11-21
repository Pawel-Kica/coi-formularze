"""Metric v2 table over cached plans (no model): old gold= next to the v2 headline, its parts and gold-free lints.

    uv run python -m bench.rescore <label>=<form>:<plan.json> [...] [--md]

Options are normalized against the form's PDF text layer (`bench/runs/_texts/<form>.pdf.json`), the same paper for
every run. Lints read the form's page Markdown from the images pipeline when one is kept in
`bench/runs/md/<form>.pdf.md` (check boxes are only visible there), else the PDF text. Plans are scored as saved
(no filter). `--md` prints a Markdown table.
"""

import json
import sys
from pathlib import Path

from app.plan import Plan
from bench.score import lints, load_designed, score_plan

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "bench" / "runs"
GOLD = ROOT / "gold" / "plans"
COLS = ("score", "v2", "fields_f1", "required_agreement", "options_paper", "conditions_recall_paper",
        "conditions_recall_designed", "conditions_precision_v2", "conditions_f1", "pages_v2", "statements_recall")
HEAD = ("gold=", "v2", "fldF1", "req", "opt/pap", "condR", "condRdes", "condP", "condF1", "pages", "stmts")
LINTS = ("vacuous", "late_trigger", "box_group_no_field", "conditional_no_condition")


def paper(form: str) -> str:
    return json.loads((RUNS / "_texts" / f"{form}.pdf.json").read_text())["text"]


def lint_paper(form: str) -> str:
    md = RUNS / "md" / f"{form}.pdf.md"
    return md.read_text() if md.exists() else paper(form)


def row(form: str, plan_path: Path) -> tuple[dict, dict]:
    plan = Plan.model_validate_json(plan_path.read_text())
    gold = Plan.model_validate_json((GOLD / f"form{form}.json").read_text())
    m = score_plan(plan, gold, paper(form), load_designed(form))["metrics"]
    return m, lints(plan, lint_paper(form))["counts"]


def main(argv: list[str]) -> int:
    md = "--md" in argv
    specs = [a for a in argv if a != "--md"]
    if not specs:
        print(__doc__)
        return 2
    out = []
    for spec in specs:
        label, rest = spec.split("=", 1)
        form, path = rest.split(":", 1)
        m, lc = row(form, Path(path).expanduser())
        n_paper, n_designed = m["conditions_n"].split("/")[1].split("+")
        na = {"conditions_recall_paper": n_paper == "0", "conditions_recall_designed": n_designed == "0"}
        cells = ["-" if na.get(k) else f"{m[k]:.2f}" for k in COLS]  # "-": the gold has no such conditions
        out.append([label, form, *cells, m["conditions_n"], "/".join(str(lc[k]) for k in LINTS)])
    head = ["run", "form", *HEAD, "conds ours/paper+des", "lints vac/late/box/cond"]
    if md:
        print("| " + " | ".join(head) + " |")
        print("|" + "---|" * len(head))
        for r in out:
            print("| " + " | ".join(r) + " |")
    else:
        w = [max(len(str(x)) for x in col) for col in zip(head, *out)]
        for r in [head, *out]:
            print("  ".join(str(x).ljust(n) for x, n in zip(r, w)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
