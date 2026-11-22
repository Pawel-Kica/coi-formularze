"""Blind A/B judge kit: the same input file through two runs, shown to a judge as X and Y.

    uv run python -I bench/blind.py make --a bench/runs/<old> --b bench/runs/<new> --out <dir> [--only 1/pdf,4/pdf]
    uv run python -I bench/blind.py add <dir> <id> --verdict X|Y|tie --defects-x N --defects-y N --reasons "..."
    uv run python -I bench/blind.py report <dir>

`make` reads two finished (or still running) runs, never the model. Both layouts work:
`bench.run_all` (`<run>/coi/<form>/<ext>.coi.json`, originals in `data/coi/<form dir>/`, id `<form>-<ext>`)
and `bench.run_dir` (`<run>/<file>.coi.json`, originals in the folder named in `<run>/source.txt`,
id = file name). For every file with a result in both runs it writes `<dir>/<id>/`:
  page-01.png ...   the original as images (as in `bench/judge_kit.py`)
  original.txt      DOC/DOCX/ODT/RTF only: macOS `textutil` text
  extracted.txt     the text the model read; `extracted-X.txt` + `extracted-Y.txt` when the runs saved different texts
  X.md, Y.md        the COI outline of each run, sides assigned at random per file
  info.md           what is in the kit
`<dir>/key.json` holds the seed and which run is X and which is Y; judges must never read it.
A file that crashed in one run still gets a kit (that side says BRAK WYNIKU): a crash is the worst regression.
Re-running `make` on the same `<dir>` keeps the sides, so earlier verdicts stay valid.
Files where both runs wrote the same COI JSON still get a kit, but `make` marks them `=` and the
report counts them as ties without a judge: don't spend a judge on them.
Convention: `--a` = OLD, `--b` = NEW; the report lists files where NEW is worse first.
The judge brief is `bench/JUDGE-BLIND.md`. Source files are untrusted: only parsed or rendered.
"""

import argparse
import json
import random
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.extract import extract
from app.rules import check
from bench.judge_kit import LEGEND, _count, outline, write_images, write_original_text

COI_SUFFIX = ".coi.json"


@dataclass
class Item:
    """One input file in one run: its original, and the COI JSON or the error the run recorded."""
    key: str  # what --only matches: "1/pdf" (run_all) or the file name (run_dir)
    src: Path
    coi: Path | None = None
    text: Path | None = None  # extracted text saved by run_dir
    error: str | None = None


# --- reading a run --------------------------------------------------------------------


def _error_rows(base: Path) -> list[dict]:
    p = base / "summary.json"
    return [r for r in json.loads(p.read_text())["rows"] if not r.get("ok")] if p.exists() else []


def run_items(run: Path) -> dict[str, Item]:
    """Kit id -> finished file of a run (a COI JSON on disk, or an error row in summary.json)."""
    items = {}
    if (run / "coi").is_dir():  # bench.run_all
        from bench.run_all import coi_files  # the same form -> original mapping the run used

        base = run / "coi"
        errors = {(r["form"], r["ext"]): r.get("error") or "brak .coi.json" for r in _error_rows(base)}
        for form, src in coi_files(None, None):
            ext = src.suffix.lower().lstrip(".")
            coi = base / form / f"{ext}{COI_SUFFIX}"
            if coi.exists():
                items[f"{form}-{ext}"] = Item(f"{form}/{ext}", src, coi=coi, text=base / form / f"{ext}.text.md")
            elif (form, ext) in errors:
                items[f"{form}-{ext}"] = Item(f"{form}/{ext}", src, error=errors[form, ext])
    elif (run / "source.txt").exists():  # bench.run_dir
        folder = Path(run.joinpath("source.txt").read_text().strip())
        for coi in sorted(run.glob(f"*{COI_SUFFIX}")):
            name = coi.name.removesuffix(COI_SUFFIX)
            items[name] = Item(name, folder / name, coi=coi, text=run / f"{name}.txt")
        for r in _error_rows(run):
            items.setdefault(r["file"], Item(r["file"], folder / r["file"], text=run / f"{r['file']}.txt",
                                             error=r.get("error") or "brak .coi.json"))
    else:
        raise SystemExit(f"{run}: neither a bench.run_all run (coi/) nor a bench.run_dir run (source.txt)")
    return {i: it for i, it in items.items() if it.src.is_file()}


# --- one kit --------------------------------------------------------------------------


def side_md(kit_id: str, side: str, item: Item) -> str:
    """One run's output as the judge sees it. Nothing here may tell the runs apart except the output itself."""
    head = [f"# {kit_id}: wynik {side}", ""]
    if not item.coi:
        return "\n".join(head + ["## BRAK WYNIKU", "", f"Pipeline nie zwrócił COI JSON: `{item.error}`", ""])
    coi = json.loads(item.coi.read_text())
    problems = check(coi)
    own, total = _count(coi)
    head += [f"- Tytuł e-usługi: {coi.get('title')}",
             f"- Pola do wypełnienia: {own} na stronach formularza, {total} razem ze stronami standardowymi",
             "- Reguły COI (import): " + ("OK" if not problems else f"{len(problems)} problemów")]
    head += [f"  - {p}" for p in problems[:10]]
    return "\n".join(head + ["", LEGEND, *outline(coi), ""])


def _saved_text(item: Item) -> str | None:
    return item.text.read_text() if item.text and item.text.exists() else None


def write_texts(kit: Path, sides: dict[str, Item]) -> str:
    """The text the model read into the kit; returns the info.md line. Runs that saved different
    texts get one file per side; a run without a saved text (older run_all runs) gets the file's own
    text now (`app.extract`: office text, PDF text layer; never the model, none for images)."""
    saved = {side: _saved_text(item) for side, item in sides.items()}
    if saved["X"] is not None and saved["Y"] is not None and saved["X"] != saved["Y"]:
        for side, text in saved.items():
            (kit / f"extracted-{side}.txt").write_text(text)
        return "extracted-X.txt, extracted-Y.txt (ekstrakcja różni się między wynikami: każdy model widział swój tekst)"
    text = saved["X"] if saved["X"] is not None else saved["Y"]
    note = "extracted.txt (tekst, który widział model)"
    if text is None:
        src = sides["X"].src
        note = "extracted.txt (dzisiejsza ekstrakcja; przebiegi nie zapisały swojego tekstu)"
        try:
            text = extract(src.read_bytes(), src.name).text
        except Exception as e:  # noqa: BLE001 - a kit without text is still a kit
            text = f"(brak: ekstrakcja tekstu nie powiodła się: {e})\n"
    (kit / "extracted.txt").write_text(text)
    return note


def make_kit(out: Path, kit_id: str, sides: dict[str, Item]) -> Path:
    kit = out / kit_id
    kit.mkdir(parents=True, exist_ok=True)
    src = sides["X"].src
    images = write_images(src, kit)
    original = write_original_text(src, kit)
    texts = write_texts(kit, sides)
    for side, item in sides.items():
        (kit / f"{side}.md").write_text(side_md(kit_id, side, item))
    (kit / "info.md").write_text("\n".join([
        f"# {kit_id}", "", f"- Oryginał jako obrazy (page-*): {images}",
        "- original.txt (textutil, niezależny odczyt oryginału): " + ("jest" if original else "brak"),
        f"- Nasza ekstrakcja: {texts}", "- Do porównania: X.md i Y.md (dwa wyniki dla tego samego pliku)", ""]))
    return kit


# --- make / add / report ----------------------------------------------------------------


def make(run_a: Path, run_b: Path, out: Path, only: list[str] | None = None, seed: int | None = None) -> list[str]:
    """Kits for every file both runs finished (and at least one produced a COI JSON). Returns the ids."""
    runs = {"a": str(run_a.resolve()), "b": str(run_b.resolve())}
    key_path = out / "key.json"
    key = json.loads(key_path.read_text()) if key_path.exists() else {**runs, "seed": seed, "files": {}}
    if (key["a"], key["b"]) != (runs["a"], runs["b"]):
        raise SystemExit(f"{out} was made for other runs ({key['a']} vs {key['b']}): use a new --out")
    if key["seed"] is None:
        key["seed"] = secrets.randbits(32)
    a, b = run_items(run_a), run_items(run_b)
    ids = [i for i in sorted(a.keys() & b.keys())
           if (a[i].coi or b[i].coi) and (not only or any(s in a[i].key or s in i for s in only))]
    out.mkdir(parents=True, exist_ok=True)
    for i in ids:
        # per-file coin from the seed and the id: stable when files are added or --only changes
        x = key["files"].get(i, {}).get("X") or random.Random(f"{key['seed']}/{i}").choice("ab")
        key["files"][i] = {"X": x, "Y": "b" if x == "a" else "a"}
        if a[i].coi and b[i].coi and json.loads(a[i].coi.read_text()) == json.loads(b[i].coi.read_text()):
            key["files"][i]["identical"] = True  # nothing to judge: the report calls it a tie
        make_kit(out, i, {"X": a[i] if x == "a" else b[i], "Y": b[i] if x == "a" else a[i]})
    key_path.write_text(json.dumps(key, ensure_ascii=False, indent=1))
    return ids


def add(out: Path, kit_id: str, verdict: str, defects_x: int, defects_y: int, reasons: str) -> int:
    """Validate one verdict and append it to `<out>/verdicts.jsonl`."""
    errs = []
    if not (out / kit_id / "X.md").is_file():
        errs.append(f"id: no kit at {out / kit_id}")
    if verdict not in ("X", "Y", "tie"):
        errs.append("verdict: X, Y or tie")
    if defects_x < 0 or defects_y < 0:
        errs.append("defects: integers >= 0")
    if not reasons.strip():
        errs.append("reasons: say why in a sentence or two")
    if errs:
        print("rejected:\n  " + "\n  ".join(errs), file=sys.stderr)
        return 1
    v = {"id": kit_id, "verdict": verdict, "defects_x": defects_x, "defects_y": defects_y, "reasons": reasons.strip()}
    with open(out / "verdicts.jsonl", "a") as f:  # one write per line: parallel judges don't interleave
        f.write(json.dumps(v, ensure_ascii=False) + "\n")
    print(f"ok {kit_id}: {verdict}")
    return 0


def unblind(out: Path) -> list[dict]:
    """One row per kit: winner "A" | "B" | "tie" | None (not judged), defects per run when a
    judge counted them. A file judged twice keeps its last verdict; an unjudged file with the same
    COI JSON in both runs is a tie. Worst regressions first: A wins, then ties, then B wins, inside
    each group the biggest growth in defects (B - A) first; unjudged files last."""
    key = json.loads((out / "key.json").read_text())
    verdicts = {}
    vpath = out / "verdicts.jsonl"
    for line in vpath.read_text().splitlines() if vpath.exists() else []:
        if line.strip():
            v = json.loads(line)
            verdicts[v["id"]] = v
    rows = []
    for kit_id, sides in key["files"].items():
        row = {"id": kit_id, "x": sides["X"].upper(), "winner": None}
        if v := verdicts.get(kit_id):
            by_run = {sides["X"]: v["defects_x"], sides["Y"]: v["defects_y"]}
            row |= {"winner": "tie" if v["verdict"] == "tie" else sides[v["verdict"]].upper(),
                    "defects_a": by_run["a"], "defects_b": by_run["b"], "reasons": v["reasons"]}
        elif sides.get("identical"):
            row |= {"winner": "tie", "reasons": "identyczny COI JSON w obu przebiegach (bez sędziego)"}
        rows.append(row)
    rank = {"A": 0, "tie": 1, "B": 2, None: 3}
    return sorted(rows, key=lambda r: (rank[r["winner"]], r.get("defects_a", 0) - r.get("defects_b", 0), r["id"]))


def report(out: Path) -> str:
    key = json.loads((out / "key.json").read_text())
    rows = unblind(out)
    judged = [r for r in rows if "defects_a" in r]
    same = sum(r["winner"] == "tie" and r not in judged for r in rows)
    wins = {w: sum(r["winner"] == w for r in rows) for w in ("A", "B", "tie")}
    da, db = sum(r["defects_a"] for r in judged), sum(r["defects_b"] for r in judged)
    name = {"A": "A", "B": "B", "tie": "remis", None: "brak werdyktu"}
    lines = [f"# Ślepe porównanie A/B: {out.name}", "",
             f"- A (stary) = `{key['a']}`", f"- B (nowy) = `{key['b']}`",
             f"- Plików: {len(rows)}, z werdyktem sędziego: {len(judged)}, identyczne w obu przebiegach "
             f"(remis bez sędziego): {same}, bez werdyktu: {sum(not r['winner'] for r in rows)}",
             f"- Wygrane: A {wins['A']}, B {wins['B']}, remisy {wins['tie']}",
             f"- Defekty razem (pliki z werdyktem sędziego): A {da}, B {db} (B - A = {db - da:+d})", "",
             "Najgorsze regresje pierwsze (wygrywa A, największy przyrost defektów w B). Defekt = coś, co "
             "urzędnik musiałby poprawić (liczy sędzia). „X to” mówi, który przebieg sędzia widział jako X "
             "(w powodach sędzia pisze o X i Y).", "",
             "| plik | lepszy | defekty A | defekty B | B - A | X to | powody |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        nums = f"{r['defects_a']} | {r['defects_b']} | {r['defects_b'] - r['defects_a']:+d}" if "defects_a" in r else " | | "
        reasons = r.get("reasons", "").replace("|", "/").replace("\n", " ")
        lines.append(f"| {r['id'].replace('|', '/')} | {name[r['winner']]} | {nums} | {r['x']} | {reasons} |")
    return "\n".join(lines + [""])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make", help="build the kits and key.json")
    m.add_argument("--a", required=True, help="run A (OLD)")
    m.add_argument("--b", required=True, help="run B (NEW)")
    m.add_argument("--out", required=True)
    m.add_argument("--only", help="comma list of substrings of <form>/<ext> or the file name, e.g. 1/pdf,4/pdf")
    m.add_argument("--seed", type=int, help="default: a fresh random seed (kept in key.json)")
    d = sub.add_parser("add", help="record one verdict")
    d.add_argument("dir")
    d.add_argument("id")
    d.add_argument("--verdict", required=True, choices=("X", "Y", "tie"))
    d.add_argument("--defects-x", required=True, type=int)
    d.add_argument("--defects-y", required=True, type=int)
    d.add_argument("--reasons", required=True)
    r = sub.add_parser("report", help="unblind and write REPORT.md")
    r.add_argument("dir")
    a = ap.parse_args()
    if a.cmd == "make":
        out = Path(a.out).expanduser()
        ids = make(Path(a.a).expanduser(), Path(a.b).expanduser(), out, a.only.split(",") if a.only else None, a.seed)
        same = {i for i, f in json.loads((out / "key.json").read_text())["files"].items() if f.get("identical")}
        for i in ids:
            print(f"kit {'=' if i in same else ' '} {i}  ({len(list((out / i).glob('page-*')))} obrazów)")
        print(f"{len(ids)} kitów w {out}, z tego {len(same & set(ids))} z identycznym COI JSON w obu przebiegach "
              "(=, remis bez sędziego); brief sędziego: bench/JUDGE-BLIND.md")
        return 0
    if a.cmd == "add":
        return add(Path(a.dir).expanduser(), a.id, a.verdict, a.defects_x, a.defects_y, a.reasons)
    out = Path(a.dir).expanduser()
    text = report(out)
    (out / "REPORT.md").write_text(text)
    print(text)
    print(out / "REPORT.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
