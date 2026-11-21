"""Paper headings for pages and sections: which part of the paper ("C. OKOLICZNOŚCI ...", "D.1 ADRES
NIERUCHOMOŚCI") each page and section of the e-service came from. Code only, no model.

    add_headings(evidence, coi, plan, doc.text, page_texts)   # in app.pipeline, after the builder

A section's anchor is the paper line of its first field quote (`app.quotes.quote_lines`, read in plan order, so a
label printed many times, "Miejscowość", resolves to the occurrence after the previous section's anchor, a line that
is exactly the quote preferred within a few lines). Its heading is the nearest heading line at or above the anchor: a Markdown `#` heading (PIPELINE=images), or a line numbered like a
paper part ("C. ...", "D.1 ...", "II. ...", mostly capitals) or all in capitals (2+ words, no item number, no full
stop). The form title and the addressee are never a heading. No such line: no heading. A page gets the heading of
its first section, if any.

Evidence entries of sections and pages gain `heading` (the line, verbatim without Markdown marks) and
`heading_page` (1-based paper page, or None). The upload page shows them under "Pokaż cytaty z papieru i warunki"
and jumps the original to that place.
"""

import re

from rapidfuzz import fuzz

from app.plan import Plan
from app.quotes import normalize, quote_lines

_MD_HEADING = re.compile(r"^\s*#{1,6}\s+")
_NUMBERED = re.compile(r"^(?:[A-ZĄĆĘŁŃÓŚŹŻ]|[IVX]{1,4})(?:\.\d+)*\.?\s+\S")
_NUMBERED_PREFIX = re.compile(r"^\S+\s+")
_MAX_CHARS = 300
_NEAR = 6  # lines


def _clean(line: str) -> str:
    """A paper line without Markdown marks: `#`, bold, table pipes (a one-cell table row is its cell)."""
    s = _MD_HEADING.sub("", line).strip()
    if "|" in s:
        cells = [c.strip() for c in s.strip("|").split("|") if c.strip()]
        s = cells[0] if len(cells) == 1 else ""
    return re.sub(r"\*\*|__", "", s).strip()


def _heading(line: str) -> str | None:
    """The heading text if `line` looks like one, else None."""
    text = _clean(line)
    if not text or len(text) > _MAX_CHARS:
        return None
    if _MD_HEADING.match(line):
        return text
    letters = [c for c in text if c.isalpha()]
    upper = sum(c.isupper() for c in letters) / len(letters) if letters else 0
    numbered = _NUMBERED.match(text)
    first_word = _NUMBERED_PREFIX.sub("", text).split(maxsplit=1)[0] if numbered else ""
    if numbered and (upper >= 0.6 or (len(first_word) >= 3 and first_word.isupper())):  # "F. DANE DO KONTAKTU (pola ...)"
        return text
    if upper >= 0.95 and len(letters) >= 8 and len(text.split()) >= 2 and not text[0].isdigit() and not text.endswith("."):
        return text  # capitals only, but not a numbered item or a sentence ("1. WYPEŁNIJ WIELKIMI LITERAMI.")
    return None


def _full_heading(lines: list[str], i: int) -> tuple[str, int]:
    """A heading broken over lines in extracted text ("D.2 MIEJSCE ... (W PRZYPADKU" / "... KOMUNALNYCH)"): the
    capitals-only line at `i` joined with the capitals lines above it, up to the numbered first one."""
    text = _heading(lines[i]) or ""
    start = i
    while not _MD_HEADING.match(lines[start]) and not _NUMBERED.match(text) and start > 0 and i - start < 4:
        above = _heading(lines[start - 1])
        if not above or _MD_HEADING.match(lines[start - 1]):
            break
        start -= 1
        text = f"{above} {text}"
    return (text, start) if start < i and _NUMBERED.match(text) else (_heading(lines[i]) or "", i)


def _lines(text: str) -> list[str]:
    """The paper's lines as `quote_lines` counts them (the ones left non-empty by `normalize`)."""
    return [line for line in text.splitlines() if normalize(line)]


def section_headings(plan: Plan, text: str) -> dict[str, tuple[str, int]]:
    """Plan section id -> (heading, its line index) for every section with a clear heading above it."""
    lines = _lines(text)
    skip = [normalize(plan.title), normalize(plan.addressee)]  # the form title and the office are no part headings
    out, cursor = {}, 0
    for page in plan.pages:
        for s in page.sections:
            anchor = None
            for f in s.fields:  # the first field found on paper, past the previous section's anchor
                hits = [a for a, _ in quote_lines(f.quote, text)]
                if hits:
                    ahead = [a for a in hits if a >= cursor] or [max(hits)]
                    # a line that is exactly the quote, a few lines on, beats a longer line that only contains it
                    exact = (a for a in ahead if a - ahead[0] <= _NEAR and normalize(lines[a]) == normalize(f.quote))
                    anchor = next(exact, ahead[0])
                    break
            if anchor is None:
                continue
            cursor = max(cursor, anchor + 1)
            for i in range(min(anchor, len(lines) - 1), -1, -1):
                if _heading(lines[i]):
                    h, start = _full_heading(lines, i)
                    if not any(n and fuzz.ratio(normalize(h), n) >= 85 for n in skip):
                        out[s.id] = (h, start)
                    break
    return out


def heading_page(heading: str, line: int, text: str, pages: list[str], start: int = 0) -> int | None:
    """1-based page of a heading line. Exact by line count when `text` is the pages joined (PIPELINE=images);
    else the first page from `start` (0-based) whose text holds the heading (text layers of a PDF)."""
    if not pages:
        return None
    counts = [len(_lines(p)) for p in pages]
    if sum(counts) == len(_lines(text)):
        for n, c in enumerate(counts, 1):
            if line < c:
                return n
            line -= c
        return None
    key = normalize(heading)
    order = list(range(start, len(pages))) + list(range(start))
    return next((i + 1 for i in order if key and key in normalize(pages[i])), None)


def add_headings(evidence: dict, coi: dict, plan: Plan, text: str, pages: list[str]) -> None:
    """Adds `heading` and `heading_page` to the evidence of every section with a paper heading (all COI sections
    built from that plan section, address twins included) and of every page whose first section has one."""
    found = section_headings(plan, text)
    paper_page: dict[str, int | None] = {}
    last = 0
    for sid, (h, line) in found.items():
        paper_page[sid] = heading_page(h, line, text, pages, last)
        last = (paper_page[sid] or last + 1) - 1
    for t in coi["tabs"]:
        first = True
        for q in t["questions"]:
            ev = evidence.get(q["field"]["id"]) or {}
            sid = ev.get("plan_id")
            if sid in found:
                ev["heading"], ev["heading_page"] = found[sid][0], paper_page[sid]
                if first and t["id"] in evidence:
                    evidence[t["id"]]["heading"], evidence[t["id"]]["heading_page"] = found[sid][0], paper_page[sid]
            first = False


def pdf_text_pages(data: bytes, filename: str) -> list[str]:
    """Text layer of every PDF page (empty for scans), for `heading_page` in the text pipeline; [] for other files."""
    if not filename.lower().endswith(".pdf"):
        return []
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
    except Exception:
        return []
    try:
        out = []
        for page in pdf:
            tp = page.get_textpage()
            out.append(tp.get_text_range())
            tp.close()
        return out
    finally:
        pdf.close()
