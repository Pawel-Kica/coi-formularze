"""One blank form in, one COI JSON out. Glue between the stages.

read (file -> page images -> Markdown by a vision model, `app.llm.transcribe`) -> llm planner (Markdown -> Plan) ->
quotes (drop items not backed by the paper) -> builder (Plan -> COI JSON; with evidence also the paper heading of each
page and section, `app.document.headings`, read from the file's real lines, `app.document.sources`) -> rules (technical checks).
"""

import logging
import time

from app.coi.builder import build_with_evidence
from app.coi.rules import check
from app.core.logging import file_context
from app.core.plan import Plan
from app.document.headings import add_headings
from app.document.sources import Paper, read_paper
from app.extract import Document
from app.grounding.quotes import filter_plan
from app.llm.planner import make_plan
from app.llm.transcribe import transcribe

log = logging.getLogger(__name__)


def convert(data: bytes, filename: str) -> dict:
    """Bytes of one blank form -> COI JSON ready to import. Raises on unreadable input."""
    return convert_with_evidence(data, filename)[0]


def convert_with_evidence(data: bytes, filename: str) -> tuple[dict, dict]:
    """The same conversion, plus the evidence map (COI element id -> paper quote, see
    `app.coi.builder.build_with_evidence`; pages and sections also get their paper heading, `app.document.headings`)
    for the upload page's editor."""
    with file_context(filename):
        started = time.monotonic()
        log.info("converting %s, %d KB", filename, len(data) // 1024)
        try:
            coi, evidence = _convert(data, filename)
        except Exception as error:
            log.warning("conversion failed after %.1f s: %s: %s", time.monotonic() - started, type(error).__name__, error)
            raise
        fields = sum(len(q.get("field", {}).get("repeatingSectionGrid", [])) for t in coi["tabs"] for q in t["questions"])
        log.info("converted in %.1f s: %d pages, %d fields in the e-service", time.monotonic() - started,
                 len(coi["tabs"]), fields)
        return coi, evidence


def _convert(data: bytes, filename: str) -> tuple[dict, dict]:
    t = time.monotonic()
    doc = transcribe(data, filename)
    log.info("read %d pages (%s), %d chars in %.1f s", len(doc.pages), doc.method, len(doc.text), time.monotonic() - t)
    t = time.monotonic()
    plan = make_plan(doc.text)
    log.info("planned in %.1f s: %s", time.monotonic() - t, _summary(plan))
    plan, dropped = filter_plan(plan, doc.text)  # THRESHOLD: the Markdown is one reading
    log.info("quote filter dropped %d items, kept %s", len(dropped), _summary(plan))
    for note in dropped:
        log.debug("dropped: %s", note)
    coi, evidence = build_with_evidence(plan)
    add_headings(evidence, coi, paper(data, filename, doc))
    problems = check(coi)
    if problems:
        raise ValueError(f"generated JSON breaks COI rules: {problems}")
    return coi, evidence


def _summary(plan: Plan) -> str:
    sections = [s for p in plan.pages for s in p.sections]
    fields = [f for s in sections for f in s.fields]
    conditions = sum(x.show_if is not None for x in [*sections, *fields])
    return (f"{len(plan.pages)} pages, {len(sections)} sections, {len(fields)} fields, {conditions} conditions, "
            f"{len(plan.statements)} statements")


def paper(data: bytes, filename: str, doc: Document) -> Paper:
    """The file's real lines for the paper headings (`app.document.sources`: PDF text layer, OCR, office text); the Markdown the
    planner read (per page) only when the file gives none."""
    try:
        found = read_paper(data, filename)
    except Exception as error:  # headings are a convenience: the conversion goes on without the file's own lines
        log.warning("could not read the file's own lines (%s: %s), headings come from the Markdown",
                    type(error).__name__, error)
        found = None
    if found and found.lines:
        return found
    return Paper.from_pages(doc.pages) if doc.pages else Paper.from_text(doc.text)
