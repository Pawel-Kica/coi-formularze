"""One blank form in, one COI JSON out. Glue between the stages.

read (file -> page images -> Markdown by a vision model, `app.llm.transcribe`) -> llm planner (Markdown -> Plan) ->
quotes (drop items not backed by the paper, plus deterministic repairs) -> builder (Plan -> COI JSON) -> rules
(technical checks). The model understands the paper, code owns the COI format.
"""

from app.builder import build_with_evidence
from app.llm.planner import make_plan
from app.llm.transcribe import transcribe
from app.quotes import filter_plan
from app.rules import check


def convert(data: bytes, filename: str) -> dict:
    """Bytes of one blank form -> COI JSON ready to import. Raises on unreadable input."""
    return convert_with_evidence(data, filename)[0]


def convert_with_evidence(data: bytes, filename: str) -> tuple[dict, dict]:
    """The same conversion, plus the evidence map (COI element id -> paper quote, see
    `app.builder.build_with_evidence`)."""
    doc = transcribe(data, filename)
    plan = make_plan(doc.text)
    plan, _dropped = filter_plan(plan, doc.text)  # THRESHOLD: the Markdown is one reading
    coi, evidence = build_with_evidence(plan)
    problems = check(coi)
    if problems:
        raise ValueError(f"generated JSON breaks COI rules: {problems}")
    return coi, evidence
