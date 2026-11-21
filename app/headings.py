"""Paper headings for pages and sections: which part of the paper ("C. OKOLICZNOŚCI ...", "D.1 ADRES
NIERUCHOMOŚCI") each page and section of the e-service came from. Code only, no model.

    add_headings(evidence, coi, paper)   # in app.pipeline after the builder; paper = app.sources.read_paper

The paper is the file's real lines (PDF text layer, OCR of a scan, an office file's text; the Markdown a vision model
wrote only when those cannot be read), so a heading the model left out of its Markdown is still found. A section's
anchor is the paper line of its first field quote, placed by `app.quotes.place_quotes` (the walk the "Źródło" crops
use too, so the two never disagree: a label printed many times, "Miejscowość", resolves to the occurrence after the
previous quote). Its heading is the nearest heading line at or above the anchor: a Markdown `#` heading, or a line
numbered like a paper part ("C. ...", "D.1 ...", "II. ...", mostly capitals) or all in capitals (2+ words, no item
number, no full stop). The form title and the addressee are never a heading. No such line: no heading. A page gets
the heading of its first section, if any.

Evidence entries of sections and pages gain `heading` (the line, verbatim without Markdown marks) and
`heading_page` (1-based paper page, or None). The upload page shows them under "Pokaż cytaty z dokumentu i warunki"
and jumps the original to that place.
"""

import re

from rapidfuzz import fuzz

from app.quotes import normalize, place_quotes, span_lines
from app.sources import Paper, quote_items

_MD_HEADING = re.compile(r"^\s*#{1,6}\s+")
_NUMBERED = re.compile(r"^(?:[A-ZĄĆĘŁŃÓŚŹŻ]|[IVX]{1,4})(?:\.\d+)*\.?\s+\S")
_NUMBERED_PREFIX = re.compile(r"^\S+\s+")
_MAX_CHARS = 300
_NEXT_ITEM = re.compile(r"\s+\d{1,3}\.\s")


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
    text = _NEXT_ITEM.split(text, 1)[0]  # a text layer line may run on into the next numbered field ("... 69. SUMA")
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


def section_headings(paper: Paper, coi: dict, evidence: dict) -> dict[str, tuple[str, int]]:
    """Plan section id (`plan_id` in the evidence, so address twins share one) -> (heading, its line index in
    `paper.lines`) for every section from the plan with a clear heading above it. The anchor is the section's own
    quote (an address block, whose fields code builds) or else its first field quote placed on paper."""
    lines = [line.text for line in paper.lines]
    places = place_quotes(quote_items(coi, evidence), paper.text)
    first = coi["tabs"][0]["questions"][0]["field"]["repeatingSectionGrid"] if coi["tabs"] and coi["tabs"][0]["questions"] else []
    addressee = (evidence.get(first[0]["field"]["id"]) or {}).get("quote") if first else None
    skip = [normalize(coi.get("title") or ""), normalize(addressee or "")]  # the form title and the office are no part headings
    out: dict[str, tuple[str, int]] = {}
    for t in coi["tabs"]:
        for q in t["questions"]:
            s = q["field"]
            key = (evidence.get(s["id"]) or {}).get("plan_id")  # sections code adds from the Standards have none
            if not key or key in out:
                continue
            ids = [s["id"], *(c["field"]["id"] for c in s.get("repeatingSectionGrid") or [])]  # an address block: its own quote
            anchor = next((span_lines(places[i], paper.text)[0] for i in ids if i in places), None)
            if anchor is None:
                continue
            for i in range(min(anchor, len(lines) - 1), -1, -1):
                if _heading(lines[i]):
                    h, start = _full_heading(lines, i)
                    if not any(n and fuzz.ratio(normalize(h), n) >= 85 for n in skip):
                        out[key] = (h, start)
                    break
    return out


def add_headings(evidence: dict, coi: dict, paper: Paper) -> None:
    """Adds `heading` and `heading_page` to the evidence of every section with a paper heading (all COI sections
    built from that plan section, address twins included) and of every page whose first section has one."""
    found = section_headings(paper, coi, evidence)
    for t in coi["tabs"]:
        for k, q in enumerate(t["questions"]):
            ev = evidence.get(q["field"]["id"]) or {}
            key = ev.get("plan_id")
            if key not in found:
                continue
            h, line = found[key]
            page = paper.lines[line].page
            ev["heading"], ev["heading_page"] = h, None if page is None else page + 1
            if k == 0 and t["id"] in evidence:
                evidence[t["id"]]["heading"], evidence[t["id"]]["heading_page"] = ev["heading"], ev["heading_page"]
