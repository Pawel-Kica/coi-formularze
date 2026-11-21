"""Plan -> full COI JSON. Pure and deterministic: the same Plan gives byte-identical JSON.

Tabs: "Dane" (predefined, from `plan.applicant`/`plan.addressee`), the Plan's
subject-matter pages, "Kontakt i zgody" (contact + consents from `plan.statements`)
and the FINISH page. Usage: `coi = build(plan)`, then `app.rules.check(coi)`.

Robust to imperfect plans (the local model writes them): choice fields without
options become text fields, conditions pointing at unknown fields/options are
dropped, option names in conditions snap to the closest real option.
"""

import re

from rapidfuzz import fuzz, process

from app.builder.core import (
    Ctx,
    answer_path,
    any_of,
    chosen,
    field,
    flow,
    negate,
    repeating_answer_path,
    section,
    tab,
)  # fmt: skip
from app.builder.standard import (
    CONTACT, DANE, FINISH, address_section, contact_page, dane_page, finish_page,
)
from app.plan import Condition, FieldType, Plan, PlanField
from app.text import ascii_fold, html_paragraphs

__all__ = ["build"]

CHOICE = {FieldType.RADIO, FieldType.CHECKBOX, FieldType.SELECT}
_COUNTRY = re.compile(r"\b(kraj|panstw|obywatelstw)")

# Extra validators detected from a TEXT_FIELD label (ascii-folded, lowercase).
_VALIDATORS = [
    ("PESEL", re.compile(r"\bpesel\b")),
    ("NIP", re.compile(r"\bnip\b")),
    ("REGON", re.compile(r"\bregon\b")),
    ("ZIP_CODE", re.compile(r"\bkod\w* pocztow")),
    ("EMAIL", re.compile(r"\be-?mail\b|\bpoczt\w* elektroniczn")),
    ("EPUAP", re.compile(r"\bepuap\b|skrytk\w* (epuap|esp)")),
    ("ADE", re.compile(r"e-?doreczen|\bade\b|adres\w* do doreczen elektronicznych")),
    ("SERIAL_NUMBER_ID_CARD", re.compile(r"dowod\w* osobist")),
]


def detect_validators(label: str) -> list[str]:
    text = ascii_fold(label).lower()
    return [name for name, rx in _VALIDATORS if rx.search(text)]


class _Loc:
    """Where a Plan field ended up in the COI JSON (for JSONata paths)."""

    def __init__(self, tab: str, section: str, tech: str, repeating: bool, type_: str, options: list[str]):
        self.tab, self.section, self.tech = tab, section, tech
        self.repeating, self.type, self.options = repeating, type_, options


def _options(f: PlanField) -> list[str]:
    seen, out = set(), []
    for o in f.options:
        o = o.strip()
        if o and o not in seen:
            seen.add(o)
            out.append(o)
    return out


def _coi_type(f: PlanField, options: list[str]) -> str:
    """Final COI type: RADIO with >5 options -> SELECT (Standards), choice without options
    -> TEXT_FIELD (except a country SELECT, which gets the Kraje dictionary)."""
    t = f.type
    if t in CHOICE and not options:
        is_country = _COUNTRY.search(ascii_fold(f.label).lower())
        return "SELECT" if is_country and t != FieldType.CHECKBOX else "TEXT_FIELD"
    if t == FieldType.RADIO and len(options) > 5:
        return "SELECT"
    return str(t)


def _snap(name: str, options: list[str]) -> str | None:
    """Condition option -> the real option name (exact, folded, then fuzzy >= 80)."""
    if name in options:
        return name
    fold = {ascii_fold(o).lower().strip(): o for o in options}
    key = ascii_fold(name).lower().strip()
    if key in fold:
        return fold[key]
    best = process.extractOne(key, list(fold), scorer=fuzz.WRatio, score_cutoff=80)
    return fold[best[0]] if best else None


def _query(cond: Condition | None, locs: dict[str, _Loc], target_section: str,
           target_field: str | None = None) -> str | None:
    """Plan condition -> JSONata SHOW query, or None if it cannot be expressed."""
    if cond is None:
        return None
    trig = locs.get(cond.field)
    if trig is None or trig.type not in ("RADIO", "CHECKBOX", "SELECT") or not trig.options:
        return None
    if target_field is None and trig.section == target_section:
        return None  # a section cannot depend on its own field
    if target_field is not None and trig.tech == target_field:
        return None
    opts = []
    for o in cond.options:
        snapped = _snap(o, trig.options)
        if snapped and snapped not in opts:
            opts.append(snapped)
    if not opts:
        return None
    if trig.repeating:
        path = repeating_answer_path(trig.tab, trig.section, trig.tech, trig.section == target_section)
    else:
        path = answer_path(trig.tab, trig.section, trig.tech)
    q = any_of(*(chosen(o, path) for o in opts))
    return negate(q) if cond.negate else q


def _page_titles(plan: Plan) -> list[str]:
    taken, out = {DANE, CONTACT, FINISH}, []
    for p in plan.pages:
        base = p.title.strip() or "Informacje"
        title, n = base, 2
        while title in taken:
            title, n = f"{base} ({n})", n + 1
        taken.add(title)
        out.append(title)
    return out


def _plan_pages(ctx: Ctx, plan: Plan) -> list[dict]:
    titles = _page_titles(plan)
    # Pass 1: techNames and locations, so conditions can point forward and backward.
    locs: dict[str, _Loc] = {}
    sec_tech: dict[int, str] = {}
    fld_tech: dict[int, str] = {}
    for title, page in zip(titles, plan.pages):
        for s in page.sections:
            sec_tech[id(s)] = st = ctx.tech(s.title)
            if s.address and not s.repeating:  # fields come from the Standards block
                continue
            for f in s.fields:
                fld_tech[id(f)] = ft = ctx.tech(f.label if f.type != FieldType.TEXT_BLOCK else f"info {f.label}")
                opts = _options(f)
                locs.setdefault(f.id, _Loc(title, st, ft, s.repeating, _coi_type(f, opts), opts))

    # Pass 2: fields with validators, options, dictionaries and states.
    tabs = []
    for title, page in zip(titles, plan.pages):
        sections = []
        for s in page.sections:
            st = sec_tech[id(s)]
            desc = html_paragraphs(s.description) if s.description.strip() else ""
            if s.address and not s.repeating:
                suffix = "_" + (st.removeprefix("adres_") or st)
                sections.append(address_section(ctx, s.title.strip(), st, suffix, registry=False,
                                                query=_query(s.show_if, locs, st), tab_title=title,
                                                description=desc, register=False))
                continue
            fields = [_field(ctx, f, fld_tech[id(f)], st, locs) for f in s.fields]
            sections.append(section(ctx, s.title.strip(), st, fields, description=desc,
                                    query=_query(s.show_if, locs, st), repeating=s.repeating))
        tabs.append(tab(ctx, title, sections))
    return tabs


def _field(ctx: Ctx, f: PlanField, tech: str, section_tech: str, locs: dict[str, _Loc]) -> dict:
    query = _query(f.show_if, locs, section_tech, tech)
    if f.type == FieldType.TEXT_BLOCK:  # label = optional heading, description = the text
        heading, text = f.label.strip(), f.description.strip()
        if not text:
            heading, text = "", heading
        return field(ctx, "TEXT_BLOCK", heading, tech, description=html_paragraphs(text), query=query)

    options = _options(f)
    type_ = _coi_type(f, options)
    required = ["REQUIRED"] if f.required else []
    desc = html_paragraphs(f.description) if f.description.strip() else ""
    label = f.label.strip()
    if type_ == "TEXT_FIELD":
        return field(ctx, type_, label, tech, description=desc, query=query,
                     validation=required + detect_validators(label))
    if type_ in ("RADIO", "CHECKBOX"):
        return field(ctx, type_, label, tech, description=desc, query=query, validation=required,
                     options=options)
    if type_ == "SELECT":
        if not options:  # country list
            extra = {"dictionaryName": "Kraje", "multiLevelTree": False, "searchEnabled": True,
                     "defaultValue": ["Polska"]}
        else:
            extra = {"searchEnabled": len(options) > 15, "defaultValue": []}
        return field(ctx, type_, label, tech, description=desc, query=query, validation=required,
                     options=options, extra=extra)
    # TEXTAREA, NUMBER, DATEPICKER, FILE_UPLOADER
    return field(ctx, type_, label, tech, description=desc, query=query, validation=required)


def build(plan: Plan) -> dict:
    """Plan -> COI JSON dict (ready to import; run `app.rules.check` on it)."""
    title = plan.title.strip()[:255]
    ctx = Ctx(title)
    # Predefined pages first, so their reference techNames win over plan labels.
    dane = dane_page(ctx, plan)
    contact = contact_page(ctx, plan.statements)
    finish = finish_page(ctx)
    tabs = [dane, *_plan_pages(ctx, plan), contact, finish]
    return {"title": title, "tabs": tabs, "flow": flow(ctx, tabs)}

