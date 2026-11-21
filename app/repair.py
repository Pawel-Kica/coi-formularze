"""Deterministic repairs of a plan after the quote filter, before the condition check.

`repair_plan(plan, text, threshold)` is called by `app.quotes.filter_plan` and fixes, in place,
what the model tends to get wrong and the paper text can settle:
- choice options that are not on the paper are dropped ("Tak" / "Nie" stay: questions made from a
  conditional sentence have them by design) when at least two real ones remain; a SELECT with mostly
  invented options becomes a TEXT_FIELD (the paper had a blank, the model made up a list);
- the same choice question asked twice (same options, quotes on the same paper line, same wording)
  is kept once, conditions move to the kept one;
- an applicant's "Adres do korespondencji" section without a condition: when the paper says it is
  filled only if it differs ("inny niż", "różni się") a Tak/Nie question is added before it and the
  section shows on "Tak"; otherwise the section goes, the "Dane" page already asks for the address.
Returns human-readable notes, like the quote filter.
"""

import re
from functools import lru_cache

from rapidfuzz import fuzz

from app.plan import Condition, FieldType, Page, Plan, PlanField, Section
from app.quotes import CHOICE_TYPES, MIN_QUOTE_CHARS, THRESHOLD_OCR, normalize, quote_found, quote_lines

YES_NO = {"tak", "nie"}
_CORRESPONDENCE = re.compile(r"adres\w* (do )?korespondenc|do korespondencji")
_SAME = re.compile(r"taki sam|ten sam|zgodny|identyczn")
_OWN_CORRESPONDENCE = re.compile(r"^adres (do korespondencji|korespondencyjny)( wnioskodawc\w*)?$")
_ONLY_IF_OTHER = re.compile(r"\binn[yae] niż\b|różni się|\bjest inny\b")
CORRESPONDENCE_QUESTION = "Czy chcesz podać inny adres do korespondencji?"
CORRESPONDENCE_TITLE = "Adres do korespondencji"  # section of the question when no earlier section can take it


@lru_cache(maxsize=16)
def _paper_stems(text: str) -> frozenset[str]:
    """Every 3-5 letter beginning of a paper word: inflection changes the endings ("skrócony" / "skróconego")."""
    return frozenset(w[:n] for w in normalize(text).split() for n in (3, 4, 5) if len(w) >= n)


def option_on_paper(option: str, text: str, threshold: float = THRESHOLD_OCR) -> bool:
    """True when the option's wording is on the paper: as a phrase (fuzzy), or every longer word of it
    in some inflected form anywhere on the paper. Options are rewritten per the Standards, so the test
    is lenient: it catches options with no footing at all (another language, made-up lists)."""
    norm = normalize(option)
    if len(norm) < MIN_QUOTE_CHARS or norm in YES_NO or quote_found(option, text, threshold):
        return True  # too short to tell ("M", "K") counts as there
    words = [w for w in norm.split() if len(w) >= 4] or norm.split()
    stems = _paper_stems(text)
    return all(w[: max(3, min(5, len(w) - 3))] in stems for w in words)


def _clean_options(plan: Plan, text: str, notes: list[str]) -> None:
    for _, sec in _sections(plan):
        for f in sec.fields:
            if f.type not in CHOICE_TYPES or not f.options:
                continue
            kept = [o for o in f.options if option_on_paper(o, text)]
            invented = [o for o in f.options if o not in kept]
            if not invented:
                continue
            if f.type == FieldType.SELECT and len(invented) > len(kept):
                notes.append(f"options: field {f.id} '{f.label}' SELECT -> TEXT_FIELD, options not on paper: {invented}")
                f.type, f.options = FieldType.TEXT_FIELD, []
            elif len(kept) >= 2 or (kept and f.type == FieldType.CHECKBOX):
                notes.append(f"options: field {f.id} '{f.label}': not on paper, dropped: {invented}")
                f.options = kept


def _sections(plan: Plan) -> list[tuple[Page, Section]]:
    return [(p, s) for p in plan.pages for s in p.sections]


def _drop_empty(plan: Plan) -> None:
    for page in plan.pages:
        page.sections = [s for s in page.sections if s.fields]
    plan.pages = [p for p in plan.pages if p.sections]


def _dedupe_questions(plan: Plan, text: str, notes: list[str]) -> None:
    """One paper question made into two choice fields: keep the first, or the one conditions use."""
    used = {t.show_if.field for _, s in _sections(plan) for t in [s, *s.fields] if t.show_if}
    choices = [(s, f) for _, s in _sections(plan) if not s.repeating for f in s.fields
               if f.type in CHOICE_TYPES and f.show_if is None]
    gone: dict[str, str] = {}
    for i, (sa, a) in enumerate(choices):
        if a.id in gone:
            continue
        for sb, b in choices[i + 1:]:
            if b.id in gone or a.options != b.options:
                continue
            la, lb = quote_lines(a.quote, text), quote_lines(b.quote, text)
            if len(la) != 1 or la != lb or fuzz.token_set_ratio(normalize(a.label), normalize(b.label)) < 85:
                continue
            keep, drop, drop_sec = (b, a, sa) if b.id in used and a.id not in used else (a, b, sb)
            gone[drop.id] = keep.id
            drop_sec.fields = [f for f in drop_sec.fields if f.id != drop.id]
            notes.append(f"duplicate: field {drop.id} '{drop.label}' repeats {keep.id}, dropped")
            if drop is a:
                break
    if gone:
        for _, s in _sections(plan):
            for t in [s, *s.fields]:
                if t.show_if and t.show_if.field in gone:
                    t.show_if.field = gone[t.show_if.field]
        _drop_empty(plan)


def _correspondence_address(plan: Plan, text: str, notes: list[str]) -> None:
    ids = {f.id for _, s in _sections(plan) for f in s.fields}
    rule = next((line.strip() for line in text.splitlines()
                 if _CORRESPONDENCE.search(line.lower()) and _ONLY_IF_OTHER.search(line.lower())), None)
    before: list[PlanField] = []  # fields of the sections already passed
    for page in plan.pages:
        out: list[Section] = []
        for sec in page.sections:
            prior, before = before, before + sec.fields
            title = normalize(sec.title)
            if sec.show_if is not None or sec.repeating or not _CORRESPONDENCE.search(title):
                out.append(sec)
            elif rule:
                # the model's own question about it, if it asked one; else ours
                ask = next((f for f in prior if f.type == FieldType.RADIO and f.show_if is None
                            and {normalize(o) for o in f.options} == YES_NO and "korespondenc" in normalize(f.label)), None)
                if ask is None:
                    fid = next(f"fk{n}" for n in range(1, 99) if f"fk{n}" not in ids)
                    ids.add(fid)
                    ask = PlanField(id=fid, label=CORRESPONDENCE_QUESTION, type=FieldType.RADIO, required=True,
                                    options=["Tak", "Nie"], quote=rule)
                    prev = out[-1] if out else None
                    if prev and not prev.address and not prev.repeating and prev.show_if is None:
                        prev.fields.append(ask)
                    else:
                        out.append(Section(id=f"s{fid}", title=CORRESPONDENCE_TITLE, fields=[ask]))
                        if title == normalize(CORRESPONDENCE_TITLE):  # two sections in a row, one title
                            sec.title = f"Inny {CORRESPONDENCE_TITLE[0].lower()}{CORRESPONDENCE_TITLE[1:]}"
                answer = next(o for o in ask.options if (normalize(o) == "nie") == bool(_SAME.search(normalize(ask.label))))
                sec.show_if = Condition(field=ask.id, options=[answer], quote=rule)
                out.append(sec)
                notes.append(f"correspondence: section {sec.id} '{sec.title}' shows only when the address differs: {rule!r}")
            elif _OWN_CORRESPONDENCE.search(title):
                notes.append(f"standard: section {sec.id} '{sec.title}' repeats the address of the Dane page, dropped")
            else:
                out.append(sec)
        page.sections = out
    plan.pages = [p for p in plan.pages if p.sections]


def repair_plan(plan: Plan, text: str) -> list[str]:
    """Run every repair on `plan` in place; returns the notes."""
    notes: list[str] = []
    _clean_options(plan, text, notes)
    _dedupe_questions(plan, text, notes)
    _correspondence_address(plan, text, notes)
    return notes
