"""Plan -> full COI JSON. Pure and deterministic: the same Plan gives byte-identical JSON.

Tabs: "Dane" (predefined, from `plan.applicant`/`plan.addressee`), the Plan's
subject-matter pages, "Kontakt i zgody" (contact + consents from `plan.statements`)
and the FINISH page. Usage: `coi = build(plan)`, then `app.rules.check(coi)`.
`coi, evidence = build_with_evidence(plan)` also says where every element came from
(the paper quote for fields, see `_evidence`); the editor on the upload page shows it.

Robust to imperfect plans (the local model writes them): choice fields without
options become text fields, conditions pointing at unknown fields/options are
dropped, option names in conditions snap to the closest real option.
"""

import re

from rapidfuzz import fuzz, process

from app.builder.core import (
    Ctx,
    all_of,
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
from app.builder.registry import address_source, pesel_attribute
from app.builder.standard import (
    CONTACT,
    DANE,
    FINISH,
    address_section,
    contact_page,
    dane_page,
    finish_page,
    own_filing,
)
from app.plan import Condition, FieldType, Plan, PlanField
from app.text import ascii_fold, html_paragraphs

__all__ = ["build", "build_with_evidence"]

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


def _plan_pages(ctx: Ctx, plan: Plan, known: dict[str, dict]) -> list[dict]:
    """The Plan's own pages; `known` gets the evidence of every element built from the Plan."""
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
    # Bonus I: a field plainly about the applicant is fed from the PESEL registers (`registry.py`).
    # While the filer may also act for someone else, it shows only for "Swoim" and an
    # editable twin covers the other answers (a read-only wrong value cannot be fixed).
    own, own_q = own_filing(plan.applicant)
    variants = [(True, own_q), (False, negate(own_q))] if own_q else [(True, None)]
    tabs = []
    for title, page in zip(titles, plan.pages):
        sections = []
        for s in page.sections:
            st = sec_tech[id(s)]
            desc = html_paragraphs(s.description) if s.description.strip() else ""
            if s.address and not s.repeating:
                source = address_source(s, title, plan.title) if own else None
                # the Standards block replaced the paper's address lines: their quotes back the section
                quote = " / ".join(dict.fromkeys(f.quote for f in s.fields))
                show_q = _query(s.show_if, locs, st)
                for i, (fed, extra_q) in enumerate(variants if source else [(False, None)]):
                    tech = st if i == 0 else ctx.tech(f"{st}_inna_osoba")
                    suffix = "_" + (tech.removeprefix("adres_") or tech)
                    built = address_section(ctx, s.title.strip(), tech, suffix,
                                            registry=source if fed else None,
                                            query=all_of(show_q, extra_q),
                                            tab_title=title, description=desc, register=False)
                    if fed:  # as in `_field`: an empty registry value must not block sending
                        for c in built["repeatingSectionGrid"]:
                            if c["field"].get("defaultValueType") == "EXTERNAL_SYSTEM":
                                c["field"]["validation"] = [v for v in c["field"]["validation"] if v != "REQUIRED"]
                    known[built["id"]] = _ev("paper", quote, plan_id=s.id, cond=s.show_if if show_q else None)
                    sections.append(built)
                continue
            fields = []
            for f in s.fields:
                attr = pesel_attribute(f, s, title) if own else None
                for i, (fed, extra_q) in enumerate(variants if attr else [(False, None)]):
                    tech = fld_tech[id(f)] if i == 0 else ctx.tech(f"{fld_tech[id(f)]}_inna_osoba")
                    built_field = _field(ctx, f, tech, st, locs, registry=attr if fed else None,
                                         extra_query=extra_q)
                    has_cond = _query(f.show_if, locs, st, tech) is not None
                    known[built_field["id"]] = _ev("paper", f.quote, plan_id=f.id,
                                                   cond=f.show_if if has_cond else None)
                    fields.append(built_field)
            built = section(ctx, s.title.strip(), st, fields, description=desc,
                            query=_query(s.show_if, locs, st), repeating=s.repeating)
            known[built["id"]] = _ev("model", plan_id=s.id, cond=s.show_if if built["states"] else None)
            sections.append(built)
        page_tab = tab(ctx, title, sections)
        known[page_tab["id"]] = _ev("model")
        tabs.append(page_tab)
    return tabs


def _field(ctx: Ctx, f: PlanField, tech: str, section_tech: str, locs: dict[str, _Loc], *,
           registry: str | None = None, extra_query: str | None = None) -> dict:
    """One Plan field -> COI field; `registry` = an EXTERNAL_SYSTEM source ("PESEL.birthDate"),
    which makes it read-only; `extra_query` is ANDed with its own SHOW condition."""
    query = all_of(_query(f.show_if, locs, section_tech, tech), extra_query)
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
    if registry:  # only TEXT_FIELD / DATEPICKER labels qualify (registry.pesel_attribute)
        # no REQUIRED: the register may lack the value and the citizen cannot type it in
        return field(ctx, type_, label, tech, description=desc, query=query, read_only=True,
                     validation=detect_validators(label) if type_ == "TEXT_FIELD" else [],
                     extra={"defaultValue": registry, "defaultValueType": "EXTERNAL_SYSTEM"})
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
    return build_with_evidence(plan)[0]


def build_with_evidence(plan: Plan) -> tuple[dict, dict]:
    """Plan -> (COI JSON, evidence). The JSON is exactly `build(plan)`; evidence is separate
    because the COI JSON stays clean (see `_evidence` for its shape)."""
    title = plan.title.strip()[:255]
    ctx = Ctx(title)
    # Predefined pages first, so their reference techNames win over plan labels.
    dane = dane_page(ctx, plan)
    contact = contact_page(ctx, plan.statements)
    finish = finish_page(ctx)
    known: dict[str, dict] = {}
    tabs = [dane, *_plan_pages(ctx, plan, known), contact, finish]
    coi = {"title": title, "tabs": tabs, "flow": flow(ctx, tabs)}
    return coi, _evidence(coi, plan, known)


def _ev(source: str, quote: str | None = None, *, plan_id: str | None = None,
        cond: Condition | None = None) -> dict:
    """One evidence entry. `page` stays None until extraction keeps page numbers: it is the hook
    for highlighting the quote on the original (element id -> quote -> page)."""
    out = {"source": source, "quote": quote, "page": None}
    if plan_id:
        out["plan_id"] = plan_id
    if cond is not None:
        out["condition_quote"] = cond.quote
    return out


def _evidence(coi: dict, plan: Plan, known: dict[str, dict]) -> dict[str, dict]:
    """Every COI element id (tab, section, field) -> where it came from, in COI order:
    - "paper": built from a Plan field (or the addressee), `quote` = its verbatim fragment of the paper;
      a SHOW condition built from the paper adds `condition_quote`;
    - "model": a page or section of the Plan (a grouping the model chose; the paper has no quote for it);
    - "code": added by code from the COI Standards (pages Dane, Kontakt i zgody, finish, address blocks).
    The CONSENT_SECTION entry lists `consent_quotes`, one per consent (None for the RODO clause)."""
    out = {}
    for t in coi["tabs"]:
        out[t["id"]] = known.get(t["id"]) or _ev("code")
        for q in t["questions"]:
            s = q["field"]
            out[s["id"]] = known.get(s["id"]) or _ev("code")
            for c in s["repeatingSectionGrid"]:
                f = c["field"]
                out[f["id"]] = known.get(f["id"]) or _ev("code")
                if f["type"] == "CONSENT_SECTION":
                    out[f["id"]]["consent_quotes"] = [None] + [st.quote for st in plan.statements]
    # The addressee field (first field of the first Dane section) holds the office named on paper.
    addressee = coi["tabs"][0]["questions"][0]["field"]["repeatingSectionGrid"][0]["field"]
    if plan.addressee.strip() and plan.addressee_quote.strip():
        out[addressee["id"]] = _ev("paper", plan.addressee_quote)
    return out

