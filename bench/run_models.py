"""Benchmark: which planner model makes the best plans (text in, the same text for every model).

For each model x form x repetition: make_plan(text) -> quote filter -> score vs the
gold plan (gold/plans/form{n}.json). Writes every plan and a summary table to
bench/runs/<timestamp>/.

    uv run python -m bench.run_models --models deepinfra/zai-org/GLM-5.2 --forms 1-7 --reps 1
    uv run python -m bench.run_models --source extract --fmt docx ...   # the file's own text via app.extract
    uv run python -m bench.run_models --summarize bench/runs/<ts>      # re-print the table
    uv run python -m bench.run_models --calibrate bench/runs/<ts>      # quote threshold stats

Text source "pdftotext" (default) = `pdftotext -layout` of the form's PDF, same input
for every model. Scoring: own fuzzy `score_plan` below ("quality") plus `bench.score.score_plan`
("score.py" column) when that module exists. `--rescore <run>` recomputes both.
"""

import argparse
import json
import statistics
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.llm import client  # noqa: E402
from app.llm.planner import make_plan  # noqa: E402
from app.plan import Plan  # noqa: E402
from app.quotes import THRESHOLD, THRESHOLD_OCR, filter_plan, normalize, quote_score  # noqa: E402

FORMS_DIR = ROOT / "data" / "coi"
GOLD_DIR = ROOT / "gold" / "plans"
RUNS_DIR = ROOT / "bench" / "runs"
LICENSES = {
    "SpeakLeash/bielik": "Apache-2.0",
    "qwen3": "Apache-2.0",
    "mistral-small": "Apache-2.0",
    "gpt-oss": "Apache-2.0",
}


def license_of(model: str) -> str:
    return next((v for k, v in LICENSES.items() if model.startswith(k)), "?")


def form_dir(n: int) -> Path:
    return next(FORMS_DIR.glob(f"{n}. *"))


def form_file(n: int, fmt: str) -> Path:
    exts = {"jpg": ["jpg", "jpeg", "jpe"], "jpeg": ["jpeg", "jpg"]}.get(fmt, [fmt])
    for f in sorted(form_dir(n).iterdir()):
        if f.suffix.lower().lstrip(".") in exts and "wizualizacja" not in f.name:
            return f
    raise FileNotFoundError(f"form {n}: no .{fmt}")


def form_text(n: int, source: str = "pdftotext", fmt: str = "pdf") -> str:
    if source == "pdftotext":
        out = subprocess.run(["pdftotext", "-layout", str(form_file(n, "pdf")), "-"],
                             capture_output=True, text=True, check=True)
        return out.stdout
    from app.extract import extract

    f = form_file(n, fmt)
    return extract(f.read_bytes(), f.name).text


# ---------- scoring (fallback when bench/score.py is missing) ----------


def _flat(plan: dict) -> list[dict]:
    """Fields with their section title and effective condition (own or section's)."""
    out = []
    for p in plan.get("pages", []):
        for s in p.get("sections", []):
            for f in s.get("fields", []):
                cond = f.get("show_if") or s.get("show_if")
                out.append({**f, "_cond": cond, "_sec": s.get("title", "")})
    return out


def _sim(a: dict, b: dict) -> float:
    lab = fuzz.WRatio(normalize(a["label"]), normalize(b["label"]))
    qa, qb = normalize(a.get("quote", "")), normalize(b.get("quote", ""))
    quo = fuzz.partial_ratio(qa, qb) if qa and qb else 0
    return 0.6 * lab + 0.4 * quo


def _match(ours: list[dict], gold: list[dict], th: float = 65) -> dict[int, int]:
    """Greedy best-first 1:1 matching gold index -> ours index."""
    pairs = sorted(((_sim(o, g), gi, oi) for gi, g in enumerate(gold) for oi, o in enumerate(ours)), reverse=True)
    m, used = {}, set()
    for s, gi, oi in pairs:
        if s < th:
            break
        if gi not in m and oi not in used:
            m[gi] = oi
            used.add(oi)
    return m


def _f1(p: float, r: float) -> float:
    return 2 * p * r / (p + r) if p + r else 0.0


def score_plan(ours: dict, gold: dict) -> dict:
    """Fuzzy plan-vs-gold scores in 0..1: fields, types, conditions, applicant, statements."""
    of, gf = _flat(ours), _flat(gold)
    m = _match(of, gf)
    fr = len(m) / len(gf) if gf else 1.0
    fp = len(m) / len(of) if of else 0.0
    types = sum(of[oi]["type"] == gf[gi]["type"] for gi, oi in m.items()) / len(m) if m else 0.0
    # conditions: a gold-conditioned field counts when ours has a condition on the matched
    # field whose trigger is the field matched to the gold trigger
    gid = {f["id"]: i for i, f in enumerate(gf)}
    oid = {f["id"]: i for i, f in enumerate(of)}
    g_cond = [gi for gi, f in enumerate(gf) if f["_cond"]]
    o_cond = [oi for oi, f in enumerate(of) if f["_cond"]]
    hit = 0
    for gi in g_cond:
        oi = m.get(gi)
        trig_g = gid.get(gf[gi]["_cond"]["field"])
        if oi is not None and of[oi]["_cond"] and m.get(trig_g) == oid.get(of[oi]["_cond"]["field"]):
            hit += 1
    if g_cond or o_cond:
        cr = hit / len(g_cond) if g_cond else 1.0
        cp = hit / len(o_cond) if o_cond else (1.0 if not g_cond else 0.0)
        cond = _f1(cp, cr) if g_cond else (1.0 if not o_cond else 0.0)
    else:
        cond = 1.0
    ga, oa = gold.get("applicant", {}), ours.get("applicant", {})
    keys = ["person", "entity", "pesel", "proxy"]
    app = (sum(bool(ga.get(k, k in ("person", "proxy"))) == bool(oa.get(k, k in ("person", "proxy"))) for k in keys)
           + (set(ga.get("entity_ids", [])) == set(oa.get("entity_ids", [])))) / 5
    gs, os_ = gold.get("statements", []), ours.get("statements", [])
    if gs or os_:
        sm = sum(any(fuzz.partial_ratio(normalize(g["quote"]), normalize(o["quote"])) >= 80 for o in os_) for g in gs)
        sr = sm / len(gs) if gs else 1.0
        sp = min(1.0, sm / len(os_)) if os_ else 0.0
        stm = _f1(sp, sr) if gs else 0.0
    else:
        stm = 1.0
    field_f1 = _f1(fp, fr)
    quality = 0.5 * field_f1 + 0.15 * types * fr + 0.15 * cond + 0.1 * app + 0.1 * stm
    return {"quality": round(quality, 3), "field_recall": round(fr, 3), "field_precision": round(fp, 3),
            "field_f1": round(field_f1, 3), "type_acc": round(types, 3), "cond_f1": round(cond, 3),
            "applicant": round(app, 3), "statements_f1": round(stm, 3), "n_ours": len(of), "n_gold": len(gf),
            "title_sim": round(fuzz.ratio(normalize(ours.get("title", "")), normalize(gold.get("title", ""))) / 100, 3)}


def score_record(rec: dict) -> None:
    """Attach scores vs gold: own fuzzy `score` and, when bench/score.py exists, its metrics as `score_ext`."""
    gold_path = GOLD_DIR / f"form{rec['form']}.json"
    if not rec.get("valid") or not gold_path.exists():
        return
    gold = json.loads(gold_path.read_text())
    if rec["filtered"] is None:
        rec["score"], rec["score_ext"] = {k: 0.0 for k in score_plan(gold, gold)}, {"score": 0.0}
        return
    rec["score"] = score_plan(rec["filtered"], gold)
    try:
        from bench.score import score_plan as external  # written by another agent
    except ImportError:
        return
    rep = external(Plan.model_validate(rec["filtered"]), Plan.model_validate(gold))
    rec["score_ext"] = rep["metrics"]
    rec["score_ext_summary"] = rep["summary"]


def apply_filter(rec: dict, text: str, th: float) -> None:
    """Quote filter on the raw plan; counts dropped items (hallucination proxy) separately from
    fields dropped because they belong to the generated Standards pages."""
    plan = Plan.model_validate(rec["plan"])
    try:
        filtered, dropped = filter_plan(plan, text, threshold=th)
    except ValueError as e:  # everything dropped: the plan is worthless, score 0
        filtered, dropped = None, [f"field *: {e}"] * sum(len(s.fields) for p in plan.pages for s in p.sections)
    rec["dropped"] = dropped
    rec["n_items"] = sum(len(s.fields) for p in plan.pages for s in p.sections) + len(plan.statements)
    rec["n_dropped_items"] = sum(d.startswith(("field", "statement")) for d in dropped)
    rec["n_dropped_standard"] = sum(d.startswith("standard") for d in dropped)
    rec["n_dropped_conditions"] = sum(d.startswith("condition") for d in dropped)
    rec["filtered"] = filtered.model_dump(mode="json") if filtered else None


def rescore(out_dir: Path) -> None:
    """Re-run the current quote filter and both scorers on saved plans (no model calls)."""
    cfg = json.loads((out_dir / "config.json").read_text())
    texts: dict[int, str] = {}
    for f in out_dir.glob("*/form*.json"):
        rec = json.loads(f.read_text())
        if rec.get("valid"):
            n = rec["form"]
            texts.setdefault(n, form_text(n, cfg["source"], cfg["fmt"]))
            apply_filter(rec, texts[n], cfg["threshold"])
            score_record(rec)
        f.write_text(json.dumps(rec, ensure_ascii=False, indent=1))
    summarize(out_dir)


# ---------- run ----------


def parse_forms(s: str) -> list[int]:
    out = []
    for part in s.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def run(models: list[str], forms: list[int], reps: int, source: str, fmt: str, tag: str) -> Path:
    out_dir = RUNS_DIR / (datetime.now().strftime("%Y%m%d-%H%M%S") + (f"-{tag}" if tag else ""))
    out_dir.mkdir(parents=True)
    texts = {n: form_text(n, source, fmt) for n in forms}
    th = THRESHOLD if source == "pdftotext" or fmt in ("pdf", "docx", "doc", "odt", "ods", "xlsx") else THRESHOLD_OCR
    (out_dir / "config.json").write_text(json.dumps(
        {"models": models, "forms": forms, "reps": reps, "source": source, "fmt": fmt, "threshold": th}, indent=1))
    for model in models:
        mdir = out_dir / model.replace("/", "_").replace(":", "_")
        mdir.mkdir()
        for n in forms:
            for rep in range(1, reps + 1):
                client.CALLS.clear()
                rec = {"model": model, "form": n, "rep": rep}
                t0 = time.monotonic()
                try:
                    plan = make_plan(texts[n], model=model)
                    rec["valid"] = True
                    rec["plan"] = plan.model_dump(mode="json")
                    apply_filter(rec, texts[n], th)
                    score_record(rec)
                except Exception as e:  # noqa: BLE001 - the bench records every failure
                    rec["valid"] = False
                    rec["error"] = f"{type(e).__name__}: {e}"
                    rec["trace"] = traceback.format_exc()[-2000:]
                rec["seconds"] = round(time.monotonic() - t0, 1)
                rec["calls"] = list(client.CALLS)
                (mdir / f"form{n}.r{rep}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1))
                q = rec.get("score", {}).get("quality"), rec.get("score_ext", {}).get("score")
                print(f"{model:45} form{n} r{rep} {rec['seconds']:7.1f}s valid={rec['valid']} "
                      f"q={q} dropped={rec.get('n_dropped_items')}/{rec.get('n_items')} {rec.get('error', '')}",
                      flush=True)
    summarize(out_dir)
    return out_dir


def summarize(out_dir: Path) -> str:
    rows = []
    for mdir in sorted(p for p in out_dir.iterdir() if p.is_dir()):
        recs = [json.loads(f.read_text()) for f in sorted(mdir.glob("form*.json"))]
        if not recs:
            continue
        model = recs[0]["model"]
        ok = [r for r in recs if r["valid"]]
        scored = [r for r in ok if "score" in r]
        by_form: dict[int, list] = {}
        for r in ok:
            by_form.setdefault(r["form"], []).append(json.dumps(r["plan"], sort_keys=True))
        multi = [v for v in by_form.values() if len(v) > 1]
        det = f"{sum(len(set(v)) == 1 for v in multi)}/{len(multi)}" if multi else "-"
        items = sum(r["n_items"] for r in ok) or 1
        mean = lambda k: statistics.mean(r["score"][k] for r in scored) if scored else float("nan")  # noqa: E731
        ext = [r["score_ext"] for r in scored if "score_ext" in r]
        mext = lambda k: statistics.mean(float(m.get(k, 0)) for m in ext) if ext else float("nan")  # noqa: E731
        per_form = {}
        for r in scored:
            per_form.setdefault(r["form"], []).append(r["score"]["quality"])
        rows.append({
            "model": model, "license": license_of(model), "valid": f"{len(ok)}/{len(recs)}",
            "ext": mext("score"), "ext_cond_r": mext("conditions_recall"), "ext_addr": mext("addressee_ok"),
            "quality": mean("quality"), "field_f1": mean("field_f1"), "type_acc": mean("type_acc"),
            "cond_f1": mean("cond_f1"), "applicant": mean("applicant"), "statements": mean("statements_f1"),
            "halluc": sum(r["n_dropped_items"] for r in ok) / items,
            "sec_per_form": statistics.mean(r["seconds"] for r in ok) if ok else float("nan"),
            "max_sec": max((r["seconds"] for r in ok), default=float("nan")),
            "determinism": det,
            "per_form": " ".join(f"{k}:{statistics.mean(v):.2f}" for k, v in sorted(per_form.items())),
        })
    head = ("| model | license | valid | score.py | quality (own) | field F1 | type acc | cond F1 | applicant | statements "
            "| cond R (score.py) | addressee ok | dropped items | s/form (mean) | s (max) | identical reruns "
            "| per form quality (own) |\n|" + "---|" * 17)
    lines = [head] + [
        f"| {r['model']} | {r['license']} | {r['valid']} | {r['ext']:.3f} | {r['quality']:.3f} | {r['field_f1']:.3f} | "
        f"{r['type_acc']:.3f} | {r['cond_f1']:.3f} | {r['applicant']:.3f} | {r['statements']:.3f} | "
        f"{r['ext_cond_r']:.3f} | {r['ext_addr']:.2f} | "
        f"{r['halluc']:.1%} | {r['sec_per_form']:.0f} | {r['max_sec']:.0f} | {r['determinism']} | {r['per_form']} |"
        for r in sorted(rows, key=lambda r: -r["quality"] if r["quality"] == r["quality"] else 0)
    ]
    table = "\n".join(lines)
    (out_dir / "summary.md").write_text(table + "\n")
    print(table)
    return table


def calibrate(run_dirs: list[Path]) -> None:
    """Quote score of every model quote vs its own form (should pass) and vs other forms (should fail)."""
    texts = {n: form_text(n) for n in range(1, 10)}
    own, other = [], []
    for d in run_dirs:
        for f in d.glob("*/form*.json"):
            r = json.loads(f.read_text())
            if not r.get("valid"):
                continue
            quotes = [fl["quote"] for p in r["plan"]["pages"] for s in p["sections"] for fl in s["fields"]]
            for q in quotes:
                own.append(quote_score(q, texts[r["form"]]))
                other += [quote_score(q, texts[m]) for m in texts if m != r["form"]]
    for th in (70, 75, 80, 85, 88, 90, 92, 95, 100):
        print(f"threshold {th}: own pass {sum(s >= th for s in own) / len(own):.1%}  "
              f"other-form pass {sum(s >= th for s in other) / len(other):.1%}")


OCR_FORMATS = {"jpg", "jpeg", "png"}


def gold_quotes(gold: dict) -> list[tuple[str, str]]:
    """(what, quote) for every quote in a gold plan: fields, conditions, statements, addressee."""
    out = [("addressee", gold.get("addressee_quote", ""))] if gold.get("addressee_quote") else []
    for p in gold["pages"]:
        for s in p["sections"]:
            if s.get("show_if"):
                out.append((f"cond {s['id']}", s["show_if"]["quote"]))
            for f in s["fields"]:
                out.append((f"field {f['id']}", f["quote"]))
                if f.get("show_if"):
                    out.append((f"cond {f['id']}", f["show_if"]["quote"]))
    out += [("statement", st["quote"]) for st in gold.get("statements", [])]
    return out


def gold_quote_drops(verbose: bool = True) -> None:
    """Per form x format: gold quotes the filter would drop (target 0). Texts from
    bench/extract/<n>/<ext>.txt (extraction agent's dumps), threshold per format type."""
    exts = sorted({f.stem for f in (ROOT / "bench" / "extract").glob("[1-9]/*.txt")})
    print("| form | " + " | ".join(exts) + " |\n|---|" + "---|" * len(exts))
    totals = dict.fromkeys(exts, 0)
    misses = []
    for n in range(1, 10):
        gold = json.loads((GOLD_DIR / f"form{n}.json").read_text())
        quotes = gold_quotes(gold)
        row = []
        for ext in exts:
            path = ROOT / "bench" / "extract" / str(n) / f"{ext}.txt"
            if not path.exists():
                row.append("-")
                continue
            text = path.read_text()
            th = THRESHOLD_OCR if ext in OCR_FORMATS else THRESHOLD
            bad = [(w, q, quote_score(q, text)) for w, q in quotes if quote_score(q, text) < th]
            totals[ext] += len(bad)
            misses += [(n, ext, w, q, round(sc)) for w, q, sc in bad]
            row.append(f"{len(bad)}/{len(quotes)}")
        print(f"| {n} | " + " | ".join(row) + " |")
    print("| total | " + " | ".join(str(totals[e]) for e in exts) + " |")
    if verbose:
        for m in misses:
            print(*m, sep="  ")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=settings.llm_model)
    ap.add_argument("--forms", default="1-9")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--source", default="pdftotext", choices=["pdftotext", "extract"])
    ap.add_argument("--fmt", default="pdf")
    ap.add_argument("--tag", default="")
    ap.add_argument("--summarize", type=Path)
    ap.add_argument("--rescore", type=Path)
    ap.add_argument("--gold-quotes", action="store_true", help="gold quotes dropped per form x format")
    ap.add_argument("--calibrate", type=Path, nargs="+")
    a = ap.parse_args()
    if a.summarize:
        summarize(a.summarize)
    elif a.gold_quotes:
        gold_quote_drops()
    elif a.rescore:
        rescore(a.rescore)
    elif a.calibrate:
        calibrate(a.calibrate)
    else:
        run(a.models.split(","), parse_forms(a.forms), a.reps, a.source, a.fmt, a.tag)


if __name__ == "__main__":
    main()
