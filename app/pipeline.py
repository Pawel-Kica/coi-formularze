"""One blank form in, one COI JSON out. Glue between the stages.

extract (file -> text) -> llm planner (text -> Plan; with LLM_VISION=1 also page images) ->
quotes (drop items not backed by the paper) -> builder (Plan -> COI JSON) -> rules (technical checks).
"""

from app.builder import build
from app.extract import extract
from app.llm.planner import make_plan
from app.llm.vision import images_only, page_images
from app.quotes import filter_plan, threshold_for
from app.rules import check


def convert(data: bytes, filename: str) -> dict:
    """Bytes of one blank form -> COI JSON ready to import. Raises on unreadable input."""
    doc = extract(data, filename)
    pages = page_images(data, filename)
    plan = make_plan(doc.text, pages)
    plan, _dropped = filter_plan(plan, doc.text, threshold_for(doc.method, images_only(pages)))
    coi = build(plan)
    problems = check(coi)
    if problems:
        raise ValueError(f"generated JSON breaks COI rules: {problems}")
    return coi
