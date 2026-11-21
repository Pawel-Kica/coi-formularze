"""Input matrix: the same COI forms through named arms (model + vision settings), one arm after
another, then one comparison table. Settles input questions by measurement: does a vision model
need our text, does grayscale help, can page images replace Tesseract on scans.

An arm is a dict of env settings incl. LLM_MODEL (`bench/matrix_arms.json`). Each arm runs
`bench.run_all` in a child process with that env (settings are read at import) into its own
folder `<out>/<arm>/`; the table comes from each arm's `coi/summary.json` and is saved as
`<out>/matrix.md` + `matrix.json`.

    uv run python -m bench.matrix --screen                    # forms 1,4,5,7, PDF + PNG scan
    uv run python -m bench.matrix                             # all 9 forms, PDF + PNG scan
    uv run python -m bench.matrix --arms vl-img,vl-img-gray --formats png
    uv run python -m bench.matrix --resume bench/runs/matrix-<time>   # files already done are skipped

Ollama: before an arm that still has files to run, every loaded model other than the arm's is
stopped (`ollama stop`) and must be gone from `ollama ps`, so two models are never in memory
together; at the end, models other than the default LLM_MODEL are stopped too. Refuses to start
while `pgrep` sees another bench or eval (one model-heavy job at a time; `--force` skips the check).
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from app.config import settings
from bench.run_all import coi_files

ROOT = Path(__file__).resolve().parent.parent
ARMS = Path(__file__).with_name("matrix_arms.json")
# The knobs the arms compare; an arm overrides them, nothing leaks in from the shell.
BASE_ENV = {"LLM_VISION": "0", "LLM_VISION_TEXT": "1", "LLM_VISION_GRAY": "0"}
SCREEN_FORMS = "1,4,5,7"
FORMATS = "pdf,png"  # the PDF and the pack's scan
OTHER_BENCH = r"bench\.|eval\.py|eval_all"
UNLOAD_WAIT_S = 60


def load_arms(path: Path, names: str | None = None) -> dict[str, dict[str, str]]:
    """Arm name -> env (string values), in file order; `names` = comma list to keep, in that order."""
    arms = {k: {e: str(v) for e, v in env.items()} for k, env in json.loads(path.read_text()).items()}
    if not names:
        return arms
    missing = [n for n in names.split(",") if n not in arms]
    if missing:
        raise SystemExit(f"unknown arm(s) {missing}, known: {list(arms)}")
    return {n: arms[n] for n in names.split(",")}


# ---------- Ollama: one model in memory ----------


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def loaded_models() -> list[str]:
    """Model names `ollama ps` lists as loaded (first column, header skipped)."""
    return [line.split()[0] for line in _run(["ollama", "ps"]).splitlines()[1:] if line.strip()]


def unload_others(keep: str | None) -> list[str]:
    """Stop every loaded model except `keep` and wait until `ollama ps` no longer lists them.
    Returns the stopped names; raises when one stays loaded."""
    others = [m for m in loaded_models() if m != keep]
    for m in others:
        print(f"ollama stop {m}", flush=True)
        _run(["ollama", "stop", m])
    deadline = time.time() + UNLOAD_WAIT_S
    while others and set(others) & set(loaded_models()):
        if time.time() > deadline:
            raise RuntimeError(f"still loaded after `ollama stop`: {set(others) & set(loaded_models())}")
        time.sleep(1)
    return others


def other_benches() -> list[str]:
    """Running bench/eval processes other than this matrix (`pgrep -fl`)."""
    r = subprocess.run(["pgrep", "-fl", OTHER_BENCH], capture_output=True, text=True, check=False)  # 1 = none
    return [line for line in r.stdout.splitlines() if "bench.matrix" not in line]


# ---------- running arms ----------


def arm_model(env: dict[str, str]) -> str:
    return env.get("LLM_MODEL") or settings.llm_model


def pending(arm_dir: Path, forms: str | None, formats: str | None) -> list[str]:
    """Files of the selection that have no saved plan + COI in this arm yet (what run_all would run)."""
    files = coi_files(set(forms.split(",")) if forms else None, set(formats.split(",")) if formats else None)
    out = []
    for form, path in files:
        ext = path.suffix.lower().lstrip(".")
        d = arm_dir / "coi" / form
        if not ((d / f"{ext}.plan.json").exists() and (d / f"{ext}.coi.json").exists()):
            out.append(f"{form}/{ext}")
    return out


def run_arm(name: str, env: dict[str, str], out: Path, forms: str | None, formats: str | None) -> list[dict]:
    """bench.run_all for one arm in a child process with the arm's env; returns its summary rows."""
    arm_dir = out / name
    todo = pending(arm_dir, forms, formats)
    print(f"\n=== arm {name}: {json.dumps(env)} ({len(todo)} files to run)", flush=True)
    if todo and (env.get("LLM_BACKEND") or settings.llm_backend) == "ollama":
        unload_others(arm_model(env))
    cmd = [sys.executable, "-m", "bench.run_all", "--out", str(arm_dir)]
    cmd += ["--forms", forms] if forms else []
    cmd += ["--formats", formats] if formats else []
    subprocess.run(cmd, env=os.environ | BASE_ENV | env, cwd=ROOT, check=False)
    summary = arm_dir / "coi" / "summary.json"
    if not summary.exists():
        print(f"arm {name}: no {summary}, run_all failed (see above)", file=sys.stderr, flush=True)
        return []
    return json.loads(summary.read_text())["rows"]


# ---------- table ----------


def expected_input(env: dict[str, str]) -> str:
    """The input kind the arm asks for (run_all `input_kind`)."""
    e = BASE_ENV | env
    return "text" if e["LLM_VISION"] != "1" else "img" if e["LLM_VISION_TEXT"] == "0" else "img+text"


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 3) if xs else None


def stats(rows: list[dict], env: dict[str, str]) -> dict:
    """Numbers for one table row: means over the ok files, per-form gold, rules, drops, seconds."""
    ok = [r for r in rows if r.get("ok")]
    want = expected_input(env)
    per_form = {}
    for r in rows:
        if not r.get("ok"):
            cell = "ERR"
        else:
            cell = f"{r['gold']:.2f}" if "gold" in r else "-"
            cell += "*" if r.get("input", want) != want else ""
        per_form[r["form"]] = cell
    dropped = [d for r in ok for d in r.get("dropped", [])]
    return {
        "files": len(rows), "ok": len(ok),
        "gold": _mean([r["gold"] for r in ok if "gold" in r]),
        "ref": _mean([r["ref"] for r in ok if "ref" in r]),
        "walid": _mean([r["walid"] for r in ok if "walid" in r]),
        "forms": per_form,
        "rules": sum(len(r.get("problems", [])) for r in ok),
        "drop_quote": sum("quote not in text" in d for d in dropped), "drop_all": len(dropped),
        "s": _mean([r["s"] for r in rows if "s" in r]),
    }


def noise(n_forms: int) -> float:
    """Arm difference on an n-form mean that is still noise: ~0.03 at 9 forms (±0.1 gold per form
    under load, bench/MODELS.md), scaled by 1/sqrt(n)."""
    return round(0.03 * (9 / max(n_forms, 1)) ** 0.5, 3)


def table(results: dict[str, list[dict]], arms: dict[str, dict[str, str]], formats: list[str]) -> tuple[str, list]:
    """Markdown table: one line per arm and format (+ "all" per arm when there are several formats)."""
    forms = sorted({r["form"] for rows in results.values() for r in rows}, key=lambda f: int(f) if f.isdigit() else 99)
    fmt = lambda x, d=3: "-" if x is None else f"{x:.{d}f}"
    head = ["arm", "format", "gold", "ref 1,5", "walid 1,5", *forms, "rules", "drops quote/all", "s/form", "ok"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    data = []
    for arm, rows in results.items():
        groups = [(f, [r for r in rows if r["ext"] == f]) for f in formats]
        if len(formats) > 1:
            groups.append(("all", rows))
        for f, rs in groups:
            if not rs:
                continue
            st = stats(rs, arms[arm])
            if f == "all":
                st["forms"] = {}
            data.append({"arm": arm, "format": f} | st)
            cells = [arm, f, fmt(st["gold"]), fmt(st["ref"]), fmt(st["walid"], 2),
                     *[st["forms"].get(x, "") for x in forms], str(st["rules"]),
                     f"{st['drop_quote']}/{st['drop_all']}", fmt(st["s"], 0), f"{st['ok']}/{st['files']}"]
            lines.append("| " + " | ".join(cells) + " |")
    lines += [
        "",
        ("gold = score_plan vs gold/plans (mean over ok files); ref/walid = forms 1 and 5 vs gold/coi; "
         "drops quote = items whose quote is not in our text, all = incl. standard-page fields."),
        ("* = the model got other input than the arm says (a file longer than LLM_VISION_MAX_PAGES keeps "
         "its text; DOC/DOCX/XLS(X)/ODS have no page images)."),
        (f"Noise: under load the same arm varies about ±0.1 gold per form (bench/MODELS.md), so arm "
         f"differences under ~{noise(len(forms))} on this {len(forms)}-form mean are noise."),
    ]
    return "\n".join(lines), data


# ---------- main ----------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--arms", help="comma list of arm names (default: all in the arms file)")
    ap.add_argument("--arms-file", default=str(ARMS))
    ap.add_argument("--forms", help="comma list, e.g. 1,5 (default: all 9)")
    ap.add_argument("--formats", default=FORMATS, help=f"comma list (default {FORMATS})")
    ap.add_argument("--screen", action="store_true", help=f"quick pruning: forms {SCREEN_FORMS}")
    ap.add_argument("--out", help="parent folder (default bench/runs/matrix-<time>), must not exist")
    ap.add_argument("--resume", help="earlier matrix folder: same arms, forms and formats, done files skipped")
    ap.add_argument("--force", action="store_true", help="start even when pgrep sees another bench/eval")
    a = ap.parse_args()

    busy = [] if a.force else other_benches()
    if busy:
        print("another bench/eval is running (one model-heavy job at a time), not starting "
              "(--force if these are not model jobs):\n  " + "\n  ".join(busy), file=sys.stderr)
        return 2
    if a.resume:
        out = Path(a.resume)
        if not (out / "config.json").exists():
            ap.error(f"--resume needs a matrix folder with config.json: {out}")
        cfg = json.loads((out / "config.json").read_text())
        print(f"resume {out}: arms, forms and formats from its config.json", flush=True)
    else:
        out = Path(a.out or f"bench/runs/matrix-{time.strftime('%Y%m%d-%H%M')}")
        if out.exists():
            ap.error(f"{out} exists: arms need fresh folders (use --resume to continue it)")
        cfg = {"arms": load_arms(Path(a.arms_file), a.arms), "forms": SCREEN_FORMS if a.screen else a.forms,
               "formats": a.formats}
        out.mkdir(parents=True)
        (out / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1))

    arms, forms, formats = cfg["arms"], cfg["forms"], cfg["formats"]
    results = {}
    try:
        for name, env in arms.items():
            results[name] = run_arm(name, env, out, forms, formats)
    finally:
        if any((e.get("LLM_BACKEND") or settings.llm_backend) == "ollama" for e in arms.values()):
            unload_others(settings.llm_model)  # leave at most the default model loaded
    md, data = table(results, arms, formats.split(","))
    (out / "matrix.md").write_text(f"# {out.name}\n\nforms: {forms or 'all'}, formats: {formats}\n\n{md}\n")
    (out / "matrix.json").write_text(json.dumps({"config": cfg, "rows": data}, ensure_ascii=False, indent=1))
    print("\n" + md + f"\n\nsaved {out / 'matrix.md'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
