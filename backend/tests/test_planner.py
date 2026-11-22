"""Planner without a model: text prep, pass-1 -> Plan, pass-2 schema and conditions."""

import json

from app.core.plan import FieldType
from app.llm import planner
from app.llm.planner import (
    apply_conditions,
    conditions_schema,
    draft_to_plan,
    prepare_text,
)

EXAMPLE = json.loads((planner.PROMPTS / "example_plan.json").read_text(encoding="utf-8"))


def test_prepare_text_shrinks_layout():
    out = prepare_text("Imię ……….……………   x\n\n\n   nr .........          działki")
    assert out == "Imię ____   x\n   nr ____   działki"


def test_example_plan_is_valid_and_gets_sequential_ids():
    p = draft_to_plan(EXAMPLE)
    ids = [f.id for pg in p.pages for s in pg.sections for f in s.fields]
    assert ids == [f"f{i}" for i in range(1, len(ids) + 1)]
    assert [s.id for pg in p.pages for s in pg.sections] == [f"s{i}" for i in range(1, 10)]


def test_example_plan_survives_quote_filter():
    from app.grounding.quotes import filter_plan

    p = draft_to_plan(EXAMPLE)
    _, dropped = filter_plan(p, (planner.PROMPTS / "example_form.txt").read_text(encoding="utf-8"))
    assert dropped == []


def test_type_fixes():
    d = {"title": "W", "addressee": "U", "pages": [{"title": "P", "sections": [{"title": "S", "repeating": False, "fields": [
        {"quote": "a", "label": "Wybór", "type": "RADIO", "required": True, "options": [], "description": ""},
        {"quote": "b", "label": "Gmina", "type": "RADIO", "required": True, "options": list("abcdefg"), "description": ""},
        {"quote": "c", "label": "Tekst", "type": "TEXT_FIELD", "required": True, "options": ["x"], "description": ""},
        {"quote": "d", "label": "", "type": "TEXT_FIELD", "required": True, "options": [], "description": ""},
    ]}]}], "statements": []}
    fields = draft_to_plan(d).pages[0].sections[0].fields
    assert [f.type for f in fields] == [FieldType.TEXT_FIELD, FieldType.SELECT, FieldType.TEXT_FIELD]
    assert fields[2].options == []


def test_conditions_schema_ties_options_to_trigger():
    p = draft_to_plan(EXAMPLE)
    schema = conditions_schema(p)
    variants = schema["properties"]["conditions"]["items"]["anyOf"]
    f1 = next(v for v in variants if v["properties"]["field"]["enum"] == ["f1"])
    assert f1["properties"]["options"]["items"]["enum"] == ["Wiosłowa", "Żaglowa", "Motorowa"]
    assert "s7" in f1["properties"]["target_id"]["enum"]
    assert any(v["properties"]["field"]["enum"] == ["q1"] for v in variants)


def test_apply_conditions_with_new_question():
    p = draft_to_plan(EXAMPLE)
    answer = json.loads((planner.PROMPTS / "example_conditions.json").read_text(encoding="utf-8"))
    out = apply_conditions(p, answer)
    secs = [s for pg in out.pages for s in pg.sections]
    q = secs[3].fields[0]  # the synthesized question got its own section right before s4
    assert q.label == "Czy łódź zostaje na przystani zimą?" and q.options == ["Tak", "Nie"]
    assert secs[4].id == "s4" and secs[4].show_if.field == q.id
    fields = {f.id: f for s in secs for f in s.fields}
    assert fields["f2"].show_if.options == ["Motorowa"]
    assert fields["f24"].show_if.field == "f22"
    assert next(s for s in secs if s.id == "s7").show_if.options == ["Inna osoba"]


def test_clean_text_and_repairs():
    from app.llm.planner import clean_text

    assert clean_text("3. Rodzaj robót:") == "Rodzaj robót"
    assert clean_text("B.1 DANE IDENTYFIKACYJNE") == "Dane identyfikacyjne"
    assert clean_text("a) jezdnia") == "Jezdnia"
    assert clean_text("PESEL") == "PESEL"
    d = {"title": "WNIOSEK O ZWROT", "addressee": "U", "pages": [{"title": "P", "sections": [{"title": "S", "fields": [
        {"quote": "a", "label": "Kwota", "type": "NUMBER", "required": True, "options": [], "description": ""},
        {"quote": "a", "label": "kwota", "type": "NUMBER", "required": True, "options": [], "description": ""},
    ]}]}], "statements": []}
    p = draft_to_plan(d)
    assert p.title == "Wniosek o zwrot"
    assert len(p.pages[0].sections[0].fields) == 1


def test_make_plan_retries_pass1_and_survives_pass2_failure(monkeypatch):
    from app.llm.client import ChatResult, LLMError

    calls = []

    def fake_chat(system, user, schema, **kw):
        calls.append(kw.get("frequency_penalty"))
        if len(calls) == 1:
            raise LLMError("model returned invalid JSON (finish_reason=length)")
        if len(calls) == 2:
            return ChatResult(data=EXAMPLE, raw="", seconds=0)
        raise LLMError("pass 2 broken")

    monkeypatch.setattr(planner, "chat_json", fake_chat)
    p = planner.make_plan("Wniosek ____")
    assert calls[1] == 0.3
    assert len(calls) == 3
    assert all(f.show_if is None for pg in p.pages for s in pg.sections for f in s.fields)


def _enums(node, path="$"):
    """Every enum in a schema with its node, for the 'typed enum' rule."""
    if isinstance(node, dict):
        if "enum" in node:
            yield path, node
        for k, v in node.items():
            yield from _enums(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _enums(v, f"{path}[{i}]")


def test_every_enum_has_a_type():
    """Some providers turn a typeless {"enum": [...]} into {} under strict json_schema."""
    plan = draft_to_plan(EXAMPLE)
    for schema in (planner.STRUCTURE_SCHEMA, conditions_schema(plan)):
        untyped = [p for p, n in _enums(schema) if n.get("type") != "string"]
        assert not untyped, untyped[:3]


def test_draft_to_plan_survives_garbage():
    """Provider garbage (dicts where strings belong, nulls, wrong containers) is repaired or dropped."""
    d = {"title": {}, "addressee": None, "addressee_quote": [], "applicant": {"person": {}, "entity_ids": [{}, "NIP"]},
         "pages": [
             {"title": "P", "sections": [{"title": "S", "description": None, "address": "yes", "repeating": None,
                                          "fields": [
                 {"quote": "a b c", "label": "Dobre", "type": {}, "required": True, "options": [{}], "description": {}},
                 {"quote": "d e f", "label": {}, "type": "TEXT_FIELD"},
                 {"quote": None, "label": "Wybór", "type": "RADIO", "options": ["Tak", {}, None, "Nie"]},
                 "not a field",
             ]}, "not a section"]},
             {"title": "Pusta", "sections": None},
         ],
         "statements": [{"quote": "x y z", "text": "Oświadczam", "required": "true"}, {"text": {}}, None]}
    p = draft_to_plan(d)
    fields = p.pages[0].sections[0].fields
    assert [(f.label, f.type) for f in fields] == [("Dobre", FieldType.TEXT_FIELD), ("Wybór", FieldType.RADIO)]
    assert fields[1].options == ["Tak", "Nie"] and fields[1].quote == ""
    assert p.title == "Wniosek" and p.addressee == "" and p.applicant.entity_ids == ["NIP"]
    assert [s.text for s in p.statements] == ["Oświadczam"]


def test_apply_conditions_survives_garbage():
    plan = draft_to_plan(EXAMPLE)
    ans = {"new_questions": [{"id": {}, "label": "X"}, None, {"id": "q1", "label": {}}],
           "conditions": [{"target_id": {}, "field": "f1", "options": ["a"]}, None,
                          {"target_id": "f2", "field": "f1", "options": [{}]}, "x"]}
    assert apply_conditions(plan, ans) == plan


def test_address_flag_only_on_pure_address_sections():
    """The builder swaps an address section's fields for the Standards block: a section that also
    holds names or dates must not be flagged, or those fields would vanish."""
    def section(labels, address=True):
        return {"title": "S", "address": address, "fields": [
            {"quote": f"q {i}", "label": lab, "type": "TEXT_FIELD"} for i, lab in enumerate(labels)]}

    d = {"title": "Wniosek", "pages": [{"title": "P", "sections": [
        section(["Imię kandydata", "Data urodzenia", "Adres zameldowania"]),
        section(["Miejscowość", "Kod pocztowy", "Ulica", "Numer budynku", "Numer lokalu"]),
        section(["Adres zamieszkania"]),
        section(["Nr domu", "Poczta", "Gmina", "Kraj"]),
    ]}]}
    assert [s.address for s in draft_to_plan(d).pages[0].sections] == [False, True, True, True]
