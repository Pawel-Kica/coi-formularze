"""One blank form in, one COI JSON out. Glue between the stages.

read (PIPELINE=text: extract file -> text; PIPELINE=images: file -> page images -> Markdown) ->
llm planner (text -> Plan; with LLM_VISION=1 also page images) -> quotes (drop items not backed by
the paper) -> builder (Plan -> COI JSON) -> rules (technical checks).
"""

from app.builder import build_with_evidence
from app.config import settings
from app.extract import Document, extract
from app.llm.planner import make_plan
from app.llm.transcribe import transcribe
from app.llm.vision import Pages, images_only, page_images
from app.quotes import filter_plan, threshold_for
from app.rules import check


def read(data: bytes, filename: str) -> tuple[Document, Pages]:
    """The text the planner reads, plus page images for it (LLM_VISION=1, text pipeline only).
    PIPELINE=images: the Markdown a vision model wrote page by page (`app.llm.transcribe`)."""
    if settings.pipeline == "images":
        return transcribe(data, filename), Pages()
    return extract(data, filename), page_images(data, filename)


def convert(data: bytes, filename: str) -> dict:
    """Bytes of one blank form -> COI JSON ready to import. Raises on unreadable input."""
    return convert_with_evidence(data, filename)[0]


def convert_with_evidence(data: bytes, filename: str) -> tuple[dict, dict]:
    """The same conversion, plus the evidence map (COI element id -> paper quote, see
    `app.builder.build_with_evidence`) for the upload page's editor."""
    doc, pages = read(data, filename)
    plan = make_plan(doc.text, pages)
    plan, _dropped = filter_plan(plan, doc.text, threshold_for(doc.method, images_only(pages)))
    coi, evidence = build_with_evidence(plan)
    problems = check(coi)
    if problems:
        raise ValueError(f"generated JSON breaks COI rules: {problems}")
    return coi, evidence
