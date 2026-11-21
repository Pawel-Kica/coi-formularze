"""Quote filter: keep only what the paper backs, and only conditions that make sense.

`filter_plan(plan, text)` runs after the model and before the builder:
- fields that belong to the pages code generates from the Standards (applicant and
  proxy data, signature, date of filling, RODO clause) are dropped even if the model
  put them in `pages`;
- every field / section / statement whose `quote` is not found in the paper text
  is dropped (proxy for "the model made it up"); sections and pages left empty go too;
- an addressee whose quote is not on paper is cleared (empty name, the office fills it in);
- every condition (`show_if`) is checked: its quote is on paper, the trigger field
  exists, is a choice field (RADIO/CHECKBOX/SELECT), its options exist (fuzzy-fixed
  to the exact option text), the trigger comes before the target. A broken
  condition is dropped, the target stays (always visible).
Returns the cleaned plan and human-readable notes for everything dropped.
"""

import re
import unicodedata
from functools import lru_cache

from rapidfuzz import fuzz, process

from app.plan import FieldType, Plan

# Calibrated in bench/run_models.py (see bench/MODELS.md).
THRESHOLD = 90.0
THRESHOLD_OCR = 85.0
MIN_QUOTE_CHARS = 3  # after normalization; shorter quotes ("a", "1)") prove nothing

CHOICE_TYPES = {FieldType.RADIO, FieldType.CHECKBOX, FieldType.SELECT}

# Built by code from the Standards ("Dane", contact + consent, finish page), never in `pages`.
_STANDARD_SECTION = re.compile(
    r"^(dane )?wnioskodawc|^twoje dane|pełnomocni|^dane kontaktowe|^kontakt|^podpis|adresat|do którego"
    r"|^dane identyfikacyjne( wnioskodawcy| składającego( deklarację)?)?$|^adres (zamieszkania|siedziby)( lub [\w ]+)?( wnioskodawcy)?$"
)
_STANDARD_LABEL = re.compile(r"pieczę|klauzul|\brodo\b|pełnomocni|wnioskodaw|^podpis$|^podpis wnioskodawcy")
_STANDARD_QUOTE = re.compile(r"podpis wnioskodawcy|czytelny podpis|^\W*podpis\W*$|pieczę|klauzula informacyjna")
_FILL_DATE = re.compile(r"^\(?data\)?$|miejscowość i data|data i podpis|^\w+, dnia$|^dnia$")

# fill-in placeholders: runs of dots/underscores/ellipsis/dashes, any underscore
_PLACEHOLDER = re.compile("[._\u2026\u00b7\u2025\\-\u2013\u2014]{3,}|_+")
# footnote marks glued to words ("ulicy1)", "PESEL *")
_FOOTNOTE = re.compile(r"(?<=[^\W\d])\d\)|\*+")
# item numbering differs per format ("5. Nazwisko" in PDF, "5. | Nazwisko" in ODS, nothing in OCR):
# "5.", "12)", "B.1", "B.1.1.", "a)", "a/"
_NUMBERING = re.compile(r"(?<!\w)(?:[a-z]\.\d+(?:\.\d+)*\.?|(?:\d+|[a-z])(?:\.\d+)*[.)/])(?!\w)")
_PUNCT = re.compile(r"[^\w\s]|_")
_SPACE = re.compile(r"\s+")
_QUOTES = str.maketrans({"\u201e": '"', "\u201d": '"', "\u201c": '"', "\u2019": "'"})


def normalize(s: str) -> str:
    """NFKC (m² = m2, ligatures split), lowercase, no fill-in placeholders, footnote marks, item numbering or punctuation,
    whitespace collapsed. Applied to both the quote and the paper text."""
    s = unicodedata.normalize("NFKC", s).lower().translate(_QUOTES)
    s = _PLACEHOLDER.sub(" ", s)
    s = _FOOTNOTE.sub(" ", s)
    s = _NUMBERING.sub(" ", s)
    s = _PUNCT.sub(" ", s)
    return _SPACE.sub(" ", s).strip()


@lru_cache(maxsize=16)
def _norm_text(text: str) -> tuple[str, list[str]]:
    t = normalize(text)
    return t, t.split()


def _window_score(q_tokens: list[str], t_tokens: list[str]) -> float:
    """OCR and multi-column layouts break a label across lines ("Rasa" ... "psa:"): find the
    quote's words close together (fuzzy per word). 100 when all words sit in one window."""
    anchor = max(q_tokens, key=len)
    w = 2 * len(q_tokens) + 8
    best = 0.0
    for i, tok in enumerate(t_tokens):
        if tok != anchor and fuzz.ratio(tok, anchor) < 85:
            continue
        window = t_tokens[max(0, i - w): i + w]
        found = sum(any(x == y or fuzz.ratio(x, y) >= 85 for y in window) for x in q_tokens)
        best = max(best, 100.0 * found / len(q_tokens))
        if best == 100.0:
            break
    return best


def quote_score(quote: str, text: str) -> float:
    """0-100: how well `quote` is found in `text` (100 = exact substring after normalize)."""
    q = normalize(quote)
    if len(q) < MIN_QUOTE_CHARS:
        return 0.0
    t, t_tokens = _norm_text(text)
    if q in t:
        return 100.0
    score = fuzz.partial_ratio(q, t)
    q_tokens = q.split()
    if score < 100 and len(q_tokens) > 1:
        win = _window_score(q_tokens, t_tokens)
        if win == 100.0:  # only a full window counts; partial windows are noise
            score = max(score, 99.0)
    return score


def quote_found(quote: str, text: str, threshold: float = THRESHOLD) -> bool:
    return quote_score(quote, text) >= threshold


def _fix_options(wanted: list[str], options: list[str]) -> list[str]:
    """Map the condition's options onto the trigger's real options (fuzzy), drop unknown."""
    norm = [normalize(o) for o in options]
    fixed = []
    for w in wanted:
        hit = process.extractOne(normalize(w), norm, scorer=fuzz.ratio)
        if hit and hit[1] >= 80 and options[hit[2]] not in fixed:
            fixed.append(options[hit[2]])
    return fixed


def filter_plan(plan: Plan, text: str, threshold: float | None = None) -> tuple[Plan, list[str]]:
    """Drop items not backed by `text` and broken conditions. Pass THRESHOLD_OCR for OCR text."""
    th = THRESHOLD if threshold is None else threshold
    plan = plan.model_copy(deep=True)
    dropped: list[str] = []

    def ok(quote: str) -> bool:
        return quote_found(quote, text, th)

    # 1. Standard-page fields, then quotes: fields, empty sections/pages, statements, addressee.
    for page in plan.pages:
        std_page = bool(_STANDARD_SECTION.search(normalize(page.title)))
        for sec in page.sections:
            kept = []
            std_sec = std_page or bool(_STANDARD_SECTION.search(normalize(sec.title)))
            for f in sec.fields:
                lab, quo = normalize(f.label), normalize(f.quote)
                # a choice field inside an applicant section is usually case data ("Składający jako: ...")
                in_std_sec = std_sec and f.type not in CHOICE_TYPES
                if in_std_sec or _STANDARD_LABEL.search(lab) or _STANDARD_QUOTE.search(quo) or _FILL_DATE.search(quo):
                    dropped.append(f"standard: field {f.id} '{f.label}' belongs to a generated page (Dane/contact/finish)")
                elif ok(f.quote):
                    kept.append(f)
                else:
                    dropped.append(f"field {f.id} '{f.label}': quote not in text: {f.quote!r}")
            sec.fields = kept
        page.sections = [s for s in page.sections if s.fields]
    plan.pages = [p for p in plan.pages if p.sections]
    statements = []
    for st in plan.statements:
        if ok(st.quote):
            statements.append(st)
        else:
            dropped.append(f"statement '{st.text[:60]}': quote not in text: {st.quote!r}")
    plan.statements = statements
    if plan.addressee and not ok(plan.addressee_quote):
        dropped.append(f"addressee {plan.addressee!r}: quote not in text: {plan.addressee_quote!r}")
        plan.addressee = plan.addressee_quote = ""
    if not plan.pages:
        raise ValueError("no page of the plan is backed by the paper text")

    # 2. Conditions. Order = position in the plan; a trigger must come before its target,
    # which also rules out cycles. A trigger that is itself conditional is fine (chain).
    order: dict[str, int] = {}
    fields = {}
    for page in plan.pages:
        for sec in page.sections:
            order[sec.id] = len(order)
            for f in sec.fields:
                order[f.id] = len(order)
                fields[f.id] = f

    def check(target_id: str, target, own_fields: set[str]) -> None:
        c = target.show_if
        if c is None:
            return
        why = None
        trig = fields.get(c.field)
        if not ok(c.quote):
            why = f"quote not in text: {c.quote!r}"
        elif trig is None:
            why = f"trigger {c.field} does not exist"
        elif trig.type not in CHOICE_TYPES or not trig.options:
            why = f"trigger {c.field} is not a choice field"
        elif c.field == target_id or c.field in own_fields:
            why = f"trigger {c.field} is inside its own target"
        elif order[c.field] > order[target_id]:
            why = f"trigger {c.field} comes after the target"
        else:
            opts = _fix_options(c.options, trig.options)
            if opts:
                c.options = opts
            else:
                why = f"options {c.options} not among {trig.options}"
        if why:
            dropped.append(f"condition on {target_id}: {why}")
            target.show_if = None

    for page in plan.pages:
        for sec in page.sections:
            check(sec.id, sec, {f.id for f in sec.fields})
            for f in sec.fields:
                check(f.id, f, set())
    return plan, dropped
