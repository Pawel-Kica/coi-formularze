"""One blank form in, one COI JSON out. Glue between the stages.

read (file -> page images -> Markdown by a vision model, `app.llm.transcribe`) -> llm planner (Markdown -> Plan) ->
quotes (drop items not backed by the paper) -> builder (Plan -> COI JSON; with evidence also the paper heading of each
page and section, `app.document.headings`, read from the file's real lines, `app.document.sources`) -> rules (technical checks).
"""

from app.coi.builder import build_with_evidence
from app.coi.rules import check
from app.document.headings import add_headings
from app.document.sources import Paper, read_paper
from app.extract import Document
from app.grounding.quotes import filter_plan
from app.llm.planner import make_plan
from app.llm.transcribe import transcribe


def convert(data: bytes, filename: str) -> dict:
    """Bytes of one blank form -> COI JSON ready to import. Raises on unreadable input."""
    return convert_with_evidence(data, filename)[0]


def convert_with_evidence(data: bytes, filename: str) -> tuple[dict, dict]:
    """The same conversion, plus the evidence map (COI element id -> paper quote, see
    `app.coi.builder.build_with_evidence`; pages and sections also get their paper heading, `app.document.headings`)
    for the upload page's editor."""
    doc = transcribe(data, filename)
    plan = make_plan(doc.text)
    plan, _dropped = filter_plan(plan, doc.text)  # THRESHOLD: the Markdown is one reading
    coi, evidence = build_with_evidence(plan)
    add_headings(evidence, coi, paper(data, filename, doc))
    problems = check(coi)
    if problems:
        raise ValueError(f"generated JSON breaks COI rules: {problems}")
    return coi, evidence


def paper(data: bytes, filename: str, doc: Document) -> Paper:
    """The file's real lines for the paper headings (`app.document.sources`: PDF text layer, OCR, office text); the Markdown the
    planner read (per page) only when the file gives none."""
    try:
        found = read_paper(data, filename)
    except Exception:
        found = None
    if found and found.lines:
        return found
    return Paper.from_pages(doc.pages) if doc.pages else Paper.from_text(doc.text)
