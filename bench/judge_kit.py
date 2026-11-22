"""Judge kit: per source file, what an AI judge needs to compare the paper with our COI JSON.

    uv run python -I bench/judge_kit.py bench/runs/drive-baseline ~/hacknation-2026/resources/drive-formularze

Reads a run dir written by `bench/run_dir.py` and writes `<run_dir>/judge/<file>/`:
  page-01.png ...  the original: PDF via pypdfium2 (110 dpi), JPG/PNG copied, other formats via
                   LibreOffice (`soffice`) when installed, otherwise no images
  original.txt     DOC/DOCX/ODT/RTF only: macOS `textutil` text, independent of our extractor
  extracted.txt    our extractor's text (what the model saw)
  ours.md          our COI JSON as the clerk would import it: pages, sections, fields (type,
                   required, options/dictionary, conditions in words, validators), plus a header
                   with run status, COI rules check and quote-filter drops
Only finished files get a kit (a `.coi.json`, or a row in `summary.json`); re-run any time, it
overwrites kits and never touches verdicts. The judge brief is `bench/JUDGE.md`; verdicts go to
`<run_dir>/judge/verdicts.jsonl`, `bench/judge_report.py` turns them into REPORT.md.
Source files are untrusted: they are only parsed or rendered, never executed.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pypdfium2 as pdfium

from app.builder.standard import CONTACT, DANE, FINISH
from app.rules import check, query_atoms
from app.text import strip_html

SKIP_NAMES = {"README.md", "source.md", ".DS_Store"}
DPI = 110
MAX_PAGES = 20
MAX_OPTIONS = 30
TEXTUTIL = {".doc", ".docx", ".odt", ".rtf"}
STANDARD_PAGES = {DANE: "predefiniowana ze Standardów COI", CONTACT: "predefiniowana ze Standardów COI",
                  FINISH: "predefiniowana, bez pól"}
LEGEND = ("Typy COI: TEXT_FIELD tekst, TEXTAREA długi tekst, NUMBER liczba, DATEPICKER data, "
          "RADIO jeden z kilku, CHECKBOX wiele z kilku, SELECT lista rozwijana, FILE_UPLOADER załącznik, "
          "TEXT_BLOCK tekst informacyjny (bez pola), CONSENT_SECTION zgody/oświadczenia do zaznaczenia.")


# --- run dir ------------------------------------------------------------------------


def summary_rows(run_dir: Path) -> dict[str, dict]:
    """`summary.json` rows by file name ({} while the run is still going)."""
    p = run_dir / "summary.json"
    return {r["file"]: r for r in json.loads(p.read_text())["rows"]} if p.exists() else {}


def plan_fields(run_dir: Path, name: str) -> int | None:
    """Field count of the saved plan, as `run_dir.py` counts it."""
    p = run_dir / f"{name}.plan.json"
    if not p.exists():
        return None
    plan = json.loads(p.read_text())
    return sum(len(s["fields"]) for pg in plan["pages"] for s in pg["sections"])


# --- original as images / independent text -------------------------------------------


def _soffice() -> str | None:
    app = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    return shutil.which("soffice") or (str(app) if app.exists() else None)


def _render_pdf(pdf: Path, out: Path) -> str:
    doc = pdfium.PdfDocument(pdf)
    n = len(doc)
    for i in range(min(n, MAX_PAGES)):
        doc[i].render(scale=DPI / 72).to_pil().save(out / f"page-{i + 1:02}.png")
    return f"{min(n, MAX_PAGES)} z {n} stron" if n > MAX_PAGES else f"{n} str."


def write_images(src: Path, out: Path) -> str:
    """Original -> out/page-NN.png. Returns a note for the ours.md header."""
    ext = src.suffix.lower()
    if ext == ".pdf":
        return _render_pdf(src, out)
    if ext in (".jpg", ".jpeg", ".png"):
        shutil.copy(src, out / f"page-01{ext}")
        return "1 obraz (kopia oryginału)"
    soffice = _soffice()
    if not soffice:
        return f"BRAK ({ext[1:].upper()} bez renderu: LibreOffice nie jest zainstalowany)"
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", tmp, str(src)],
                       capture_output=True, timeout=120)
        pdf = Path(tmp) / f"{src.stem}.pdf"
        return _render_pdf(pdf, out) + " (render LibreOffice)" if pdf.exists() else "BRAK (LibreOffice nie wyrenderował)"


def write_original_text(src: Path, out: Path) -> bool:
    """DOC/DOCX/ODT/RTF -> out/original.txt via macOS textutil (a second, independent reading)."""
    if src.suffix.lower() not in TEXTUTIL or not shutil.which("textutil"):
        return False
    r = subprocess.run(["textutil", "-convert", "txt", "-stdout", str(src)], capture_output=True, timeout=60)
    if r.returncode or not r.stdout.strip():
        return False
    (out / "original.txt").write_bytes(r.stdout)
    return True


# --- COI JSON -> outline -------------------------------------------------------------


def _kids(s: dict) -> list[dict]:
    return [c["field"] for c in s.get("repeatingSectionGrid") or []]


def _label(f: dict) -> str:
    return (f.get("title") or "").strip() or f"[{f.get('type')}]"


def _short(html: str, n: int) -> str:
    t = re.sub(r"\s+", " ", strip_html(html or "")).strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def _index(coi: dict) -> dict:
    """(tab title, section techName, field techName) -> field title, for conditions."""
    return {(t["title"], q["field"].get("techName"), k.get("techName")): _label(k)
            for t in coi.get("tabs", []) for q in t.get("questions", []) for k in _kids(q["field"])}


def condition_words(query: str, index: dict) -> str:
    """JSONata SHOW query -> `„Tak” w „Czy …?”` joined with i/lub/NIE, structure kept."""
    atoms, _ = query_atoms(query or "")
    if not atoms:
        return f"nieczytelny warunek: {query[:150]}"
    out = query
    for a in sorted(atoms, key=lambda a: a.start, reverse=True):
        opt, tab, sec, fld = a
        name = index.get((tab, sec, fld))
        where = f"„{name}”" if name else f"??? ({tab}/{sec}/{fld}, nie ma takiego pola)"
        row = " (ten sam wiersz)" if a.same_row else ""
        out = out[: a.start] + f"„{opt}” w {where}{row}" + out[a.start + len(a.text):]
    out = re.sub(r"\$not\s*", "NIE ", out)
    out = re.sub(r"\band\b", "i", re.sub(r"\bor\b", "lub", out))
    out = re.sub(r"\s+", " ", out).strip()
    while out.startswith("(") and out.endswith(")") and out.count("(") == 1:
        out = out[1:-1]
    return out


def _states(f: dict, index: dict) -> list[str]:
    kind = {"SHOW": "pokaż gdy", "READ_ONLY": "tylko do odczytu gdy"}
    return [f"{kind.get(st.get('type'), st.get('type'))}: {condition_words(st.get('query', ''), index)}"
            for st in f.get("states") or []]


def _attrs(f: dict) -> list[str]:
    """Type, required, options/dictionary, validators, default, read-only."""
    val = f.get("validation") or []
    parts = [f.get("type", "?")]
    if "REQUIRED" in val:
        parts.append("wymagane")
    if f.get("dictionaryName"):
        parts.append(f"słownik {f['dictionaryName']}")
    opts = [o.get("name", "") for o in f.get("options") or []]
    if opts:
        more = f" (+{len(opts) - MAX_OPTIONS})" if len(opts) > MAX_OPTIONS else ""
        parts.append("opcje: " + " | ".join(opts[:MAX_OPTIONS]) + more)
    if extra := [v for v in val if v != "REQUIRED"]:
        parts.append("walidatory: " + ", ".join(extra))
    dv = f.get("defaultValue")
    if f.get("defaultValueType") == "EXTERNAL_SYSTEM" and dv:
        parts.append(f"z rejestru {dv[0] if isinstance(dv, list) else dv}")
    elif dv not in (None, "", []):
        parts.append(f"domyślnie: {', '.join(dv) if isinstance(dv, list) else dv}")
    elif f.get("readOnly") and f.get("type") == "TEXT_FIELD":
        parts.append("pusta wartość")  # e.g. office name when the paper names none
    if f.get("readOnly"):
        parts.append("tylko do odczytu")
    return parts


def _tag(f: dict, ids: bool) -> str:
    """` `techName`` after an element's name when the outline shows ids (the clerk-corrections judge cites them)."""
    return f" `{f.get('techName')}`" if ids and f.get("techName") else ""


def _field_lines(f: dict, index: dict, ids: bool = False) -> list[str]:
    t = f.get("type")
    if t == "TEXT_BLOCK":
        head = (f.get("title") or "").strip()
        lines = [f"- [tekst informacyjny]{' ' + head + ':' if head else ''}{_tag(f, ids)} {_short(f.get('description'), 300)}"]
    elif t == "CONSENT_SECTION":
        consents = (f.get("consentSectionConfig") or {}).get("consents") or []
        lines = [f"- CONSENT_SECTION{_tag(f, ids)}, {len(consents)} do zaznaczenia (pierwsza to RODO ze Standardów):"]
        lines += [f"  - [{'wymagana' if c.get('required') else 'opcjonalna'}] {_short(c.get('description'), 400)}"
                  for c in consents]
    else:
        lines = [f"- **{_label(f)}**{_tag(f, ids)} · " + " · ".join(_attrs(f))]
        if desc := _short(f.get("description"), 250):
            lines.append(f"  - opis: {desc}")
    return lines + [f"  - {s}" for s in _states(f, index)]


def _is_address(kids: list[dict]) -> bool:
    dicts = {k.get("dictionaryName") or "" for k in kids}
    return "Kraje" in dicts and any(d.startswith("TERYT") for d in dicts)


def _address_line(kids: list[dict]) -> str:
    """Standards address block in one line: field names tagged PL / zagranica by their condition."""
    names = []
    for k in kids:
        atoms = [a for st in k.get("states") or [] for a in query_atoms(st.get("query", ""))[0]]
        tag = ""
        if any(a[0] == "Polska" for a in atoms):
            tag = " (zagranica)" if all(a.negated for a in atoms) else " (PL)"
        name = k.get("title") or "?"
        if k.get("dictionaryName", "").startswith("TERYT"):
            name = f"{k['dictionaryName']} (województwo → gmina → miejscowość)"
        names.append(name + ("*" if "REQUIRED" in (k.get("validation") or []) else "") + tag)
    src = " · wszystko z rejestru MLPZ, tylko do odczytu" if all(k.get("readOnly") for k in kids) else ""
    return f"- [blok adresu ze Standardów COI, * = wymagane] {', '.join(names)}{src}"


def outline(coi: dict, ids: bool = False) -> list[str]:
    """COI JSON -> markdown lines: pages -> sections -> fields; `ids` adds each element's techName."""
    index = _index(coi)
    tabs = coi.get("tabs", [])
    lines = []
    for i, t in enumerate(tabs, 1):
        std = STANDARD_PAGES.get(t.get("title"))
        lines += ["", f"## Strona {i}/{len(tabs)}: {t.get('title')}" + (f" ({std})" if std else "")]
        for q in t.get("questions", []):
            s = q["field"]
            kids = _kids(s)
            kind = "Sekcja powtarzalna (wiersze dodaje użytkownik)" if s.get("type") == "REPEATING_SECTION" else "Sekcja"
            lines += ["", f"### {kind}: {_label(s)}{_tag(s, ids)}"]
            lines += [f"- _{st}_" for st in _states(s, index)]
            if desc := _short(s.get("description"), 300):
                lines.append(f"- _opis sekcji: {desc}_")
            if _is_address(kids):
                lines.append(_address_line(kids))
                continue
            for k in kids:
                lines += _field_lines(k, index, ids)
    return lines


def _count(coi: dict) -> tuple[int, int]:
    """(input fields on the form's own pages, input fields in total); TEXT_BLOCK not counted."""
    own = total = 0
    for t in coi.get("tabs", []):
        for q in t.get("questions", []):
            n = sum(k.get("type") != "TEXT_BLOCK" for k in _kids(q["field"]))
            total += n
            own += n if t.get("title") not in STANDARD_PAGES else 0
    return own, total


def ours_md(name: str, run_dir: Path, row: dict, images: str, original_txt: bool) -> str:
    coi_path = run_dir / f"{name}.coi.json"
    texts = ["extracted.txt (nasza ekstrakcja)"] + (["original.txt (textutil, niezależny odczyt)"] if original_txt else [])
    head = [f"# {name}", "", f"- Obrazy oryginału: {images}", f"- Teksty: {', '.join(texts)}",
            f"- Metoda ekstrakcji: {row.get('method', '?')} · czas pipeline: {row.get('s', '?')} s"]
    if not coi_path.exists():
        err = row.get("error") or "brak .coi.json"
        return "\n".join(head + ["", "## BRAK WYNIKU", "", f"Pipeline nie zwrócił COI JSON: `{err}`", ""])
    coi = json.loads(coi_path.read_text())
    problems = check(coi)
    own, total = _count(coi)
    head += [f"- Tytuł e-usługi: {coi.get('title')}",
             f"- Pola do wypełnienia: {own} na stronach formularza, {total} razem ze stronami standardowymi",
             "- Reguły COI (import): " + ("OK" if not problems else f"{len(problems)} problemów")]
    head += [f"  - {p}" for p in problems[:10]]
    dropped = row.get("dropped")
    if dropped is None:
        head.append("- Odrzucone przez filtr cytatów: brak danych (summary.json jeszcze nie istnieje)")
    else:
        head.append(f"- Odrzucone przez filtr cytatów (cytatu nie ma w tekście): {len(dropped)}")
        head += [f"  - {d}" for d in dropped[:15]]
    return "\n".join(head + ["", LEGEND, *outline(coi), ""])


# --- main --------------------------------------------------------------------------


def make_kit(src: Path, run_dir: Path, row: dict) -> Path:
    out = run_dir / "judge" / src.name
    out.mkdir(parents=True, exist_ok=True)
    images = write_images(src, out)
    original = write_original_text(src, out)
    txt = run_dir / f"{src.name}.txt"
    (out / "extracted.txt").write_text(txt.read_text() if txt.exists() else "(brak: ekstrakcja tekstu nie powiodła się)\n")
    (out / "ours.md").write_text(ours_md(src.name, run_dir, row, images, original))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("src_folder")
    a = ap.parse_args()
    run_dir, src_dir = Path(a.run_dir).expanduser(), Path(a.src_folder).expanduser()
    rows = summary_rows(run_dir)
    files = sorted(f for f in src_dir.iterdir() if f.is_file() and f.name not in SKIP_NAMES)
    done, waiting = 0, []
    for f in files:
        if not (run_dir / f"{f.name}.coi.json").exists() and f.name not in rows:
            waiting.append(f.name)
            continue
        out = make_kit(f, run_dir, rows.get(f.name, {}))
        print(f"kit {out.relative_to(run_dir)}  ({len(list(out.glob('page-*')))} obrazów)")
        done += 1
    print(f"{done} kitów w {run_dir / 'judge'}; jeszcze bez wyniku: {len(waiting)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
