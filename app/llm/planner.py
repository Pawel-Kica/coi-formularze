"""Planner: paper form text -> Plan, with a local model via Ollama (two passes).

Pass 1 (structure): text -> pages/sections/fields/statements/applicant/addressee,
  no ids and no conditions. Output constrained by a JSON Schema (always parses).
  Code assigns stable ids (s1.., f1.. in plan order) and fixes field types.
Pass 2 (dependencies): text + compact listing of the pass-1 plan -> conditions,
  with a schema whose enums only allow existing target ids, existing choice-field
  ids and each field's own options. May add synthesized Tak/Nie trigger questions.

Used by the pipeline as `make_plan(text)`; the quote filter (`app.quotes`) runs after.
"""

import json
import logging
import re
from functools import cache
from pathlib import Path

from pydantic import ValidationError

from app.llm.client import LLMError, chat_json
from app.plan import Condition, FieldType, Page, Plan, PlanField, Section

log = logging.getLogger(__name__)
PROMPTS = Path(__file__).parent / "prompts"
CHOICE = {FieldType.RADIO, FieldType.CHECKBOX, FieldType.SELECT}
MAX_NEW_QUESTIONS = 3
# Repeated after the form text: small models follow rules better when they are the last thing read.
REMINDER = (
    "Pamiętaj: w `pages` NIE ma danych wnioskodawcy (imię, nazwisko, nazwa, adres, PESEL, NIP, telefon, e-mail), "
    "danych pełnomocnika, adresata, daty, miejscowości wypełnienia ani podpisu. Każde pole ma dosłowny `quote`."
)


@cache
def _prompt(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


# ---------- input text ----------

_FILL = re.compile(r"[.…_·]{3,}(?:[ .…_·]*[.…_·]{2,})*")
_SPACES = re.compile(r"[ \t ]{3,}")


def prepare_text(text: str) -> str:
    """Shrink layout text for the model: fill-in dots/underscores -> `____`, wide gaps -> 3 spaces, no blank lines."""
    lines = []
    for line in text.replace("\f", "\n").splitlines():
        line = _SPACES.sub("   ", _FILL.sub("____", line)).rstrip()
        if line.strip():
            lines.append(line)
    return "\n".join(lines)


# ---------- pass 1: structure ----------

_STR = {"type": "string"}
_BOOL = {"type": "boolean"}

STRUCTURE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": _STR,
        "addressee": _STR,
        "addressee_quote": _STR,
        "applicant": {
            "type": "object",
            "properties": {
                "person": _BOOL,
                "entity": _BOOL,
                "entity_ids": {"type": "array", "items": {"enum": ["NIP", "REGON", "KRS"]}},
                "pesel": _BOOL,
                "proxy": _BOOL,
                "registry_extract": _BOOL,
            },
            "required": ["person", "entity", "entity_ids", "pesel", "proxy", "registry_extract"],
        },
        "pages": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "title": _STR,
                    "sections": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": _STR,
                                "description": _STR,
                                "address": _BOOL,
                                "repeating": _BOOL,
                                "fields": {
                                    "type": "array",
                                    "minItems": 1,
                                    "items": {
                                        "type": "object",
                                        # quote first: the model anchors on the paper before naming the field
                                        "properties": {
                                            "quote": _STR,
                                            "label": _STR,
                                            "type": {"enum": [t.value for t in FieldType]},
                                            "required": _BOOL,
                                            "options": {"type": "array", "items": _STR},
                                            "description": _STR,
                                        },
                                        "required": ["quote", "label", "type", "required", "options", "description"],
                                    },
                                },
                            },
                            "required": ["title", "description", "address", "repeating", "fields"],
                        },
                    },
                },
                "required": ["title", "sections"],
            },
        },
        "statements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"quote": _STR, "text": _STR, "required": _BOOL},
                "required": ["quote", "text", "required"],
            },
        },
    },
    "required": ["title", "addressee", "addressee_quote", "applicant", "pages", "statements"],
}


def _structure_system() -> str:
    return (
        _prompt("structure.md")
        + "\n\n# Przykład (INNY formularz: pokazuje tylko format i styl, nie przenoś z niego pól ani cytatów)\n\nTekst:\n```\n"
        + _prompt("example_form.txt")
        + "```\n\nPlan:\n```json\n"
        + json.dumps(json.loads(_prompt("example_plan.json")), ensure_ascii=False)
        + "\n```\n"
    )


_LEAD_NUM = re.compile(r"^\s*(?:(?:[A-Za-z]\.)?\d+(?:\.\d+)*[.)]?|[a-zA-Z][.)/])\s+")
TITLE_STARTS = ("Wniosek", "Zgłoszenie", "Deklaracja", "Oświadczenie")


def clean_text(s: str) -> str:
    """Model text -> display text: no paper numbering ("3.", "B.1", "a)"), no trailing colon,
    no SHOUTING, first letter upper-case."""
    s = _LEAD_NUM.sub("", (s or "").strip()).strip().rstrip(":").strip()
    if " " in s and s.isupper():  # "DANE WNIOSKU" yes, "PESEL" no
        s = s.lower()
    return s[:1].upper() + s[1:]


def _fix_type(f: dict) -> tuple[FieldType, list[str]]:
    """Deterministic type fixes: options only on choice fields, RADIO <= 5 options < SELECT."""
    t = FieldType(f["type"])
    opts = list(dict.fromkeys(clean_text(o) for o in f.get("options") or [] if o and o.strip()))
    if t in CHOICE:
        if not opts:
            return FieldType.TEXT_FIELD, []
        if t == FieldType.RADIO and len(opts) > 5:
            t = FieldType.SELECT
        elif t == FieldType.SELECT and len(opts) <= 5:
            t = FieldType.RADIO
        return t, opts
    return t, []


def draft_to_plan(d: dict) -> Plan:
    """Pass-1 JSON -> Plan, repaired: ids s1../f1.. in plan order, cleaned labels/options,
    duplicate fields within a section and empty sections/pages dropped, title normalized."""
    n_sec = n_field = 0
    pages = []
    for p in d.get("pages", []):
        sections = []
        for s in p.get("sections", []):
            fields, seen = [], set()
            for f in s.get("fields", []):
                label = clean_text(f.get("label", ""))
                key = (label.lower(), f.get("quote", ""), f.get("type"))
                if not label or key in seen:
                    continue
                seen.add(key)
                n_field += 1
                t, opts = _fix_type(f)
                fields.append(
                    PlanField(id=f"f{n_field}", label=label, type=t, required=bool(f.get("required")), options=opts,
                              description=(f.get("description") or "").strip(), quote=f.get("quote", ""))
                )
            if fields:
                n_sec += 1
                sections.append(
                    Section(id=f"s{n_sec}", title=clean_text(s.get("title", "")) or fields[0].label,
                            description=(s.get("description") or "").strip(), address=bool(s.get("address")),
                            repeating=bool(s.get("repeating")), fields=fields)
                )
        if sections:
            pages.append(Page(title=clean_text(p.get("title", "")) or sections[0].title, sections=sections))
    title = clean_text(d.get("title", ""))
    if not title.startswith(TITLE_STARTS):
        title = f"Wniosek {title[:1].lower()}{title[1:]}" if title.lower().startswith(("o ", "w sprawie")) else title
    applicant = {k: v for k, v in (d.get("applicant") or {}).items() if v is not None}
    statements = [
        {**st, "text": clean_text(st["text"])} for st in d.get("statements", []) if (st.get("text") or "").strip()
    ]
    return Plan.model_validate(
        {
            "title": title or "Wniosek",
            "addressee": (d.get("addressee") or "").strip(),
            "addressee_quote": d.get("addressee_quote") or "",
            "applicant": applicant,
            "pages": pages,
            "statements": statements,
        }
    )


# ---------- pass 2: conditions ----------


def plan_listing(plan: Plan) -> str:
    """Compact text view of the plan for pass 2: ids, types, labels, options."""
    out = []
    for p in plan.pages:
        out.append(f"Strona: {p.title}")
        for s in p.sections:
            flags = (" (powtarzalna)" if s.repeating else "") + (" (adres)" if s.address else "")
            out.append(f"  {s.id} sekcja \"{s.title}\"{flags}")
            for f in s.fields:
                opts = f" opcje: {json.dumps(f.options, ensure_ascii=False)}" if f.options else ""
                out.append(f"    {f.id} {f.type.value} \"{f.label}\"{opts}")
    return "\n".join(out)


def conditions_schema(plan: Plan) -> dict | None:
    """Schema for pass 2: enums tie every condition to a real target, trigger and its options."""
    targets = [s.id for p in plan.pages for s in p.sections] + [
        f.id for p in plan.pages for s in p.sections for f in s.fields
    ]
    triggers = [(f.id, f.options) for p in plan.pages for s in p.sections for f in s.fields if f.type in CHOICE]
    triggers += [(f"q{i}", ["Tak", "Nie"]) for i in range(1, MAX_NEW_QUESTIONS + 1)]
    variants = [
        {
            "type": "object",
            "properties": {
                "target_id": {"enum": targets},
                "field": {"enum": [fid]},
                "options": {"type": "array", "minItems": 1, "items": {"enum": opts}},
                "negate": _BOOL,
                "quote": _STR,
            },
            "required": ["target_id", "field", "options", "negate", "quote"],
        }
        for fid, opts in triggers
    ]
    return {
        "type": "object",
        "properties": {
            "new_questions": {
                "type": "array",
                "maxItems": MAX_NEW_QUESTIONS,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"enum": [f"q{i}" for i in range(1, MAX_NEW_QUESTIONS + 1)]},
                        "label": _STR,
                        "place_before": {"enum": targets},
                        "quote": _STR,
                    },
                    "required": ["id", "label", "place_before", "quote"],
                },
            },
            "conditions": {"type": "array", "items": {"anyOf": variants}},
        },
        "required": ["new_questions", "conditions"],
    }


def _conditions_system() -> str:
    ex_plan = draft_to_plan(json.loads(_prompt("example_plan.json")))
    return (
        _prompt("conditions.md")
        + "\n\n# Przykład (inny formularz)\n\nTekst:\n```\n"
        + _prompt("example_form.txt")
        + "```\n\nPlan:\n```\n"
        + plan_listing(ex_plan)
        + "\n```\n\nOdpowiedź:\n```json\n"
        + json.dumps(json.loads(_prompt("example_conditions.json")), ensure_ascii=False)
        + "\n```\n"
    )


def apply_conditions(plan: Plan, answer: dict) -> Plan:
    """Insert synthesized trigger questions, then attach conditions (first one per target wins).

    Structural checks (trigger before target, options exist...) are done again by
    `app.quotes.filter_plan`; here we only skip what cannot be attached at all.
    """
    plan = plan.model_copy(deep=True)
    n_field = sum(len(s.fields) for p in plan.pages for s in p.sections)
    n_sec = sum(len(p.sections) for p in plan.pages)
    rename: dict[str, str] = {}
    for q in answer.get("new_questions", []):
        qid, before = q.get("id"), q.get("place_before")
        if qid in rename or not q.get("label", "").strip():
            continue
        n_field += 1
        new = PlanField(id=f"f{n_field}", label=q["label"].strip(), type=FieldType.RADIO, required=True,
                        options=["Tak", "Nie"], quote=q.get("quote", ""))
        for p in plan.pages:
            for i, s in enumerate(p.sections):
                if s.id == before:  # target is a whole section: the question gets its own section before it
                    n_sec += 1
                    p.sections.insert(i, Section(id=f"s{n_sec}", title=new.label, fields=[new]))
                    rename[qid] = new.id
                    break
                ids = [f.id for f in s.fields]
                if before in ids:
                    s.fields.insert(ids.index(before), new)
                    rename[qid] = new.id
                    break
            if qid in rename:
                break

    items = {s.id: s for p in plan.pages for s in p.sections}
    items |= {f.id: f for p in plan.pages for s in p.sections for f in s.fields}
    for c in answer.get("conditions", []):
        target = items.get(c.get("target_id"))
        trig = rename.get(c.get("field"), c.get("field"))
        if target is None or target.show_if is not None or trig not in items or trig == target.id:
            continue
        target.show_if = Condition(field=trig, options=c["options"], negate=bool(c.get("negate")),
                                   quote=c.get("quote", ""))
    return plan


# ---------- entry point ----------


def make_plan(text: str, model: str | None = None) -> Plan:
    """Paper form text -> Plan (not yet quote-filtered). Deterministic: temperature 0, fixed seed.

    Pass 1 is retried once with a frequency penalty when its answer is cut off or unusable
    (a looping model); a failed pass 2 leaves the plan without conditions.
    """
    src = prepare_text(text)
    sys1 = _structure_system()
    user1 = f"Tekst formularza:\n```\n{src}\n```\n\n{REMINDER}"
    try:
        plan = draft_to_plan(chat_json(sys1, user1, STRUCTURE_SCHEMA, model=model, name="plan").data)
    except (LLMError, ValidationError) as e:
        log.warning("pass 1 failed (%s), retrying with a frequency penalty", e)
        plan = draft_to_plan(
            chat_json(sys1, user1, STRUCTURE_SCHEMA, model=model, name="plan", frequency_penalty=0.3).data
        )
    sys2 = _conditions_system()
    user2 = f"Tekst formularza:\n```\n{src}\n```\n\nPlan:\n```\n{plan_listing(plan)}\n```"
    try:
        r2 = chat_json(sys2, user2, conditions_schema(plan), model=model, name="conditions", max_tokens=4000)
        return apply_conditions(plan, r2.data)
    except (LLMError, ValidationError) as e:
        log.warning("pass 2 failed (%s), plan has no conditions", e)
        return plan
