"""Bonus II benchmark: new dictionary proposals vs the hand-written key in `gold/dictionaries/`.

Runs the real step (page images -> Markdown -> model candidates -> code filter) on one file per gold
form, saves `<out>/<id>.json` (Markdown, raw candidates + kept + dropped), prints the table
form / expected / found / junk, and checks the bar: >= 80% of expected found overall
and <= 1 junk proposal per form.

    uv run python -m bench.dictionaries                      # all gold forms, Eden models
    uv run python -m bench.dictionaries --only form7,bip22
    uv run python -m bench.dictionaries --rescore bench/runs/dict-...   # re-score saved runs, no model

Gold file: `expected` (must be found) and `acceptable` (defensible, not junk), each entry
with `match` (regex over the normalized name + field + quote of a proposal) and optional
`values_any` (the proposal must list one of these values, to tell apart two lists on
the same topic). An expected `kind: "puste_pole"` (blank field) matches only proposals
without values. A kept proposal that matches nothing is junk. A second proposal for an
already found entry counts as fine (same dictionary, another field).
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

from app.dictionaries import filter_candidates
from app.extract import extract
from app.llm.dictionaries import ask_candidates
from app.llm.transcribe import transcribe
from app.quotes import THRESHOLD, THRESHOLD_OCR, normalize

ROOT = Path(__file__).resolve().parent.parent
GOLD = ROOT / "gold" / "dictionaries"
ROOTS = {"coi": ROOT / "data" / "coi", "bip": Path.home() / "hacknation-2026" / "resources" / "bip-formularze"}
MIN_FOUND = 0.8
MAX_JUNK = 1


def gold_files(only: set[str] | None) -> list[dict]:
    golds = [json.loads(p.read_text()) for p in sorted(GOLD.glob("*.json"))]
    return [g for g in golds if not only or g["id"] in only]


def form_file(g: dict) -> Path:
    hits = sorted(p for p in ROOTS[g["root"]].glob(g["file"]) if p.name != "source.md")
    if not hits:
        raise FileNotFoundError(f"{g['id']}: no file for {g['file']}")
    return hits[0]


def matches(entry: dict, p: dict) -> bool:
    hay = normalize(f"{p['name']} {p['field']['label']} {p['field']['quote']}")
    if not re.search(entry["match"], hay):
        return False
    if entry.get("kind") == "puste_pole" and p["items"]:  # a blank field has no values on paper: a list is something else
        return False
    want = entry.get("values_any")
    if not want:
        return True
    values = [normalize(i["nazwa"]) for i in p["items"]]
    return any(normalize(w) in v for w in want for v in values)


def score(g: dict, proposals: list[dict]) -> dict:
    found: set[str] = set()
    junk: list[str] = []
    for p in proposals:
        hit = next((e for e in g["expected"] if matches(e, p)), None)
        if hit:
            found.add(hit["name"])
        elif not any(matches(e, p) for e in g["acceptable"]):
            junk.append(p["name"])
    return {"expected": [e["name"] for e in g["expected"]], "found": sorted(found),
            "missed": [e["name"] for e in g["expected"] if e["name"] not in found], "junk": junk}


def run_one(g: dict, out: Path) -> dict:
    path = form_file(g)
    t0 = time.monotonic()
    doc = transcribe(path.read_bytes(), path.name)
    candidates = ask_candidates(doc.text)
    kept, dropped = filter_candidates(candidates, doc.text)
    rec = {"id": g["id"], "file": str(path), "method": doc.method, "seconds": round(time.monotonic() - t0, 1),
           "text": doc.text, "candidates": candidates, "proposals": kept, "dropped": dropped}
    (out / f"{g['id']}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1))
    return rec


def report(rows: list[tuple[dict, dict]]) -> bool:
    print(f"\n{'formularz':10} {'oczek.':>6} {'znal.':>5} {'śmieci':>6} {'czas':>6}  szczegóły")
    exp_total = found_total = 0
    junk_ok = True
    for g, rec in rows:
        s = score(g, rec["proposals"])
        exp_total += len(s["expected"])
        found_total += len(s["found"])
        junk_ok &= len(s["junk"]) <= MAX_JUNK
        detail = "; ".join(filter(None, [
            f"znalezione: {', '.join(s['found'])}" if s["found"] else "",
            f"brak: {', '.join(s['missed'])}" if s["missed"] else "",
            f"śmieci: {', '.join(s['junk'])}" if s["junk"] else "",
            f"inne OK: {len(rec['proposals']) - len(s['junk']) - len(s['found'])}"
            if len(rec["proposals"]) - len(s["junk"]) - len(s["found"]) > 0 else "",
        ]))
        print(f"{g['id']:10} {len(s['expected']):>6} {len(s['found']):>5} {len(s['junk']):>6} {rec['seconds']:>5}s  {detail}")
    ratio = found_total / exp_total if exp_total else 1.0
    passed = ratio >= MIN_FOUND and junk_ok
    print(f"\nznalezione {found_total}/{exp_total} = {ratio:.0%} (próg {MIN_FOUND:.0%}), "
          f"śmieci <= {MAX_JUNK} na formularz: {'tak' if junk_ok else 'NIE'} -> {'ZALICZONE' if passed else 'NIEZALICZONE'}")
    return passed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma list of gold ids, e.g. form7,bip22")
    ap.add_argument("--rescore", help="saved run dir: re-filter saved candidates and score, no model")
    ap.add_argument("--out", default=f"bench/runs/dict-{time.strftime('%Y%m%d-%H%M')}")
    a = ap.parse_args()
    golds = gold_files(set(a.only.split(",")) if a.only else None)
    rows = []
    if a.rescore:
        for g in golds:
            p = Path(a.rescore) / f"{g['id']}.json"
            if p.exists():
                rec = json.loads(p.read_text())
                if "text" in rec:  # the Markdown the model read
                    text, th = rec["text"], THRESHOLD
                else:  # an older run read the file's own text (OCR for scans): office/PDF text only now
                    path = Path(rec["file"])
                    text = extract(path.read_bytes(), path.name).text
                    th = THRESHOLD_OCR if "ocr" in rec.get("method", "") else THRESHOLD
                rec["proposals"], rec["dropped"] = filter_candidates(rec["candidates"], text, th)
                rows.append((g, rec))
    else:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        for g in golds:
            rec = run_one(g, out)
            print(f"{g['id']:8} {rec['seconds']:>6}s kept={len(rec['proposals'])} dropped={len(rec['dropped'])}", flush=True)
            rows.append((g, rec))
        print(f"zapisane w {out}")
    return 0 if report(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
