"""Planner: paper form text -> Plan, with the configured model (`app.llm.client`, two passes).

Pass 1 (structure): text -> pages/sections/fields/statements/applicant/addressee,
  no ids and no conditions. Output constrained by a JSON Schema (always parses).
  Code assigns stable ids (s1.., f1.. in plan order) and fixes field types.
Pass 2 (dependencies): text + compact listing of the pass-1 plan -> conditions,
  with a schema whose enums only allow existing target ids, existing choice-field
  ids and each field's own options. May add synthesized Tak/Nie trigger questions.

The text is the Markdown `app.llm.transcribe` wrote from the page images; the planner (LLM_MODEL) reads
text only. Used by the pipeline as `make_plan(text)`; the quote filter (`app.quotes`) runs after.
"""

import json
import logging
import re
from functools import cache
from pathlib import Path

from pydantic import ValidationError

from app.llm.client import LLMError, chat_json
from app.plan import (
    Applicant,
    Condition,
    EntityId,
    FieldType,
    Page,
    Plan,
    PlanField,
    Section,
)

log = logging.getLogger(__name__)
PROMPTS = Path(__file__).parent / "prompts"
CHOICE = {FieldType.RADIO, FieldType.CHECKBOX, FieldType.SELECT}
MAX_NEW_QUESTIONS = 3
# The task, after the (long) form text; the rules live in the system prompt.
TASK = "Zaprojektuj plan e-usługi dla tego formularza."


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


def _enum(values: list[str]) -> dict:
    """Typed enum: some providers turn a typeless {"enum": [...]} into {} under strict mode."""
    return {"type": "string", "enum": list(values)}

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
                "entity_ids": {"type": "array", "items": _enum([e.value for e in EntityId])},
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
                                            "type": _enum([t.value for t in FieldType]),
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


def _str(x) -> str:
    return x.strip() if isinstance(x, str) else ""


def _bool(x) -> bool:
    return x is True or (isinstance(x, str) and x.strip().lower() == "true")


def _dicts(x) -> list[dict]:
    return [i for i in x if isinstance(i, dict)] if isinstance(x, list) else []


def _fix_type(f: dict) -> tuple[FieldType, list[str]]:
    """Deterministic type fixes: options only on choice fields, RADIO <= 5 options < SELECT.
    An unknown type (provider garbage) becomes RADIO when there are options, else TEXT_FIELD."""
    raw = f.get("options")
    opts = list(dict.fromkeys(clean_text(o) for o in (raw if isinstance(raw, list) else []) if _str(o)))
    try:
        t = FieldType(f.get("type"))
    except ValueError:
        t = FieldType.RADIO if opts else FieldType.TEXT_FIELD
    if t in CHOICE:
        if not opts:
            return FieldType.TEXT_FIELD, []
        if t == FieldType.RADIO and len(opts) > 5:
            t = FieldType.SELECT
        elif t == FieldType.SELECT and len(opts) <= 5:
            t = FieldType.RADIO
        return t, opts
    return t, []


# Labels of postal address lines; `address` stays only on sections made of these alone.
_ADDRESS_LABEL = re.compile(
    r"adres|miejscowo|kod|ulic|numer (budynku|domu|lokalu|nieruchomo|mieszkania)|^nr |poczta|gmina|powiat"
    r"|wojew|kraj|teryt",
    re.IGNORECASE,
)


def _is_address(fields: list[PlanField]) -> bool:
    return all(_ADDRESS_LABEL.search(f.label) for f in fields)


def _applicant(a) -> dict:
    """Only well-typed keys survive; the rest falls back to the Applicant defaults."""
    if not isinstance(a, dict):
        return {}
    out = {k: v for k, v in a.items() if k in Applicant.model_fields and isinstance(v, bool)}
    if isinstance(a.get("entity_ids"), list):
        out["entity_ids"] = list(dict.fromkeys(i for i in a["entity_ids"] if isinstance(i, str) and i in EntityId.__members__))
    return out


def draft_to_plan(d: dict) -> Plan:
    """Pass-1 JSON -> Plan, repaired: ids s1../f1.. in plan order, cleaned labels/options,
    duplicate fields within a section and empty sections/pages dropped, title normalized,
    `address` kept only on sections whose fields are all address lines. Never crashes on provider garbage (dicts or nulls where strings belong, wrong containers):
    such values are treated as missing, items left without a label or text are dropped."""
    n_sec = n_field = 0
    pages = []
    for p in _dicts(d.get("pages")):
        sections = []
        for s in _dicts(p.get("sections")):
            fields, seen = [], set()
            for f in _dicts(s.get("fields")):
                label = clean_text(_str(f.get("label")))
                quote = _str(f.get("quote"))
                key = (label.lower(), quote, str(f.get("type")))
                if not label or key in seen:
                    continue
                seen.add(key)
                n_field += 1
                t, opts = _fix_type(f)
                fields.append(
                    PlanField(id=f"f{n_field}", label=label, type=t, required=_bool(f.get("required")), options=opts,
                              description=_str(f.get("description")), quote=quote)
                )
            if fields:
                n_sec += 1
                sections.append(
                    Section(id=f"s{n_sec}", title=clean_text(_str(s.get("title"))) or fields[0].label,
                            description=_str(s.get("description")),
                            address=_bool(s.get("address")) and _is_address(fields),
                            repeating=_bool(s.get("repeating")), fields=fields)
                )
        if sections:
            pages.append(Page(title=clean_text(_str(p.get("title"))) or sections[0].title, sections=sections))
    title = clean_text(_str(d.get("title")))
    if not title.startswith(TITLE_STARTS):
        title = f"Wniosek {title[:1].lower()}{title[1:]}" if title.lower().startswith(("o ", "w sprawie")) else title
    statements = [
        {"text": clean_text(_str(st.get("text"))), "quote": _str(st.get("quote")),
         "required": st.get("required") is None or _bool(st.get("required"))}
        for st in _dicts(d.get("statements")) if _str(st.get("text"))
    ]
    return Plan.model_validate(
        {
            "title": title or "Wniosek",
            "addressee": _str(d.get("addressee")),
            "addressee_quote": _str(d.get("addressee_quote")),
            "applicant": _applicant(d.get("applicant")),
            "pages": pages,
            "statements": statements,
        }
    )


# ---------- pass 2: conditions ----------


def plan_listing(plan: Plan) -> str:
    """Compact text view of the plan for pass 2: ids, types, labels, options, and the paper text
    each field comes from (so conditional sentences on paper can be tied to plan ids)."""
    out = []
    for p in plan.pages:
        out.append(f"Strona: {p.title}")
        for s in p.sections:
            flags = (" (powtarzalna)" if s.repeating else "") + (" (adres)" if s.address else "")
            out.append(f"  {s.id} sekcja \"{s.title}\"{flags}")
            for f in s.fields:
                opts = f" opcje: {json.dumps(f.options, ensure_ascii=False)}" if f.options else ""
                quote = " ".join(f.quote.split())
                src = f" ← papier: \"{quote[:100]}{'…' if len(quote) > 100 else ''}\"" if quote else ""
                out.append(f"    {f.id} {f.type.value} \"{f.label}\"{opts}{src}")
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
                "target_id": _enum(targets),
                "field": _enum([fid]),
                "options": {"type": "array", "minItems": 1, "items": _enum(opts)},
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
                        "id": _enum([f"q{i}" for i in range(1, MAX_NEW_QUESTIONS + 1)]),
                        "label": _STR,
                        "place_before": _enum(targets),
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
    for q in _dicts(answer.get("new_questions")):
        qid, before = _str(q.get("id")), q.get("place_before")
        if not qid or qid in rename or not _str(q.get("label")):
            continue
        n_field += 1
        new = PlanField(id=f"f{n_field}", label=_str(q["label"]), type=FieldType.RADIO, required=True,
                        options=["Tak", "Nie"], quote=_str(q.get("quote")))
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
    for c in _dicts(answer.get("conditions")):
        target = items.get(_str(c.get("target_id")))
        trig = _str(c.get("field"))
        trig = rename.get(trig, trig)
        opts = [o for o in c.get("options") or [] if _str(o)] if isinstance(c.get("options"), list) else []
        if target is None or target.show_if is not None or trig not in items or trig == target.id or not opts:
            continue
        target.show_if = Condition(field=trig, options=opts, negate=_bool(c.get("negate")), quote=_str(c.get("quote")))
    return plan


# ---------- entry point ----------


def make_plan(text: str, model: str | None = None) -> Plan:
    """Paper form text -> Plan, not yet quote-filtered. Deterministic: temperature 0, fixed seed.

    Pass 1 is retried once with a frequency penalty when its answer is cut off or unusable
    (a looping model); a failed pass 2 leaves the plan without conditions.
    """
    form_text = f"Tekst formularza:\n```\n{prepare_text(text)}\n```\n\n"
    sys1 = _structure_system()
    user1 = form_text + TASK
    try:
        plan = draft_to_plan(chat_json(sys1, user1, STRUCTURE_SCHEMA, model=model, name="plan").data)
    except (LLMError, ValidationError) as e:
        log.warning("pass 1 failed (%s), retrying with a frequency penalty", e)
        plan = draft_to_plan(
            chat_json(sys1, user1, STRUCTURE_SCHEMA, model=model, name="plan", frequency_penalty=0.3).data
        )
    user2 = form_text + f"Plan:\n```\n{plan_listing(plan)}\n```"
    try:
        r2 = chat_json(_conditions_system(), user2, conditions_schema(plan), model=model, name="conditions",
                       max_tokens=4000)
        return apply_conditions(plan, r2.data)
    except (LLMError, ValidationError) as e:
        log.warning("pass 2 failed (%s), plan has no conditions", e)
        return plan
