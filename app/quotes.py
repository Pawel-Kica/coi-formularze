"""Quote filter: keep only what the paper backs, and only conditions that make sense.

`filter_plan(plan, text)` runs after the model and before the builder:
- fields that belong to the pages code generates from the Standards (applicant and
  proxy data, the applicant's own address, signature, date of filling, RODO clause) are
  dropped even if the model put them in `pages`; attachments (FILE_UPLOADER) and fields the
  paper fills only in some case ("wypełnić jeżeli ...") are case data and stay even under
  such a page or section title;
- a negated condition over every option of its trigger (never shown once answered) is
  cleared before the repairs, so they see the section as ungated; so is a plain condition over
  every option of a required single-choice trigger that is always shown (always true);
- every field / section / statement whose `quote` is not found in the paper text
  is dropped (proxy for "the model made it up"); sections and pages left empty go too;
- an addressee whose quote is not on paper is cleared (empty name, the office fills it in);
- every condition (`show_if`) is checked: its quote is on paper, the trigger field
  exists, is a choice field (RADIO/CHECKBOX/SELECT), its options exist (fuzzy-fixed
  to the exact option text), the trigger comes before the target (a trigger found after
  its target is first moved right before it, `_hoist_triggers`), and the quote is more
  than the trigger's own question or option line unless the target sits right there on
  paper (`_supported`). A broken condition is dropped, the target stays (always visible;
  a field also stops being required);
- between the two, `app.repair.repair_plan` fixes invented options, doubled questions and
  the applicant's correspondence address;
- last, a field shown by its own condition becomes required when shown (`app.repair.require_shown`).
Returns the cleaned plan and human-readable notes for everything dropped.
"""

import re
import unicodedata
from bisect import bisect_right
from functools import lru_cache

from rapidfuzz import fuzz, process

from app.plan import FieldType, Plan, Section

# Calibrated in bench/run_models.py (see bench/MODELS.md).
THRESHOLD = 90.0
THRESHOLD_OCR = 85.0
MIN_QUOTE_CHARS = 3  # after normalization; shorter quotes ("a", "1)") prove nothing


def threshold_for(method: str) -> float:
    """Threshold for `filter_plan` by the reading method of the text: THRESHOLD_OCR for OCR text (the methods
    "pdf-ocr" / "ocr" of earlier bench runs), else THRESHOLD."""
    return THRESHOLD_OCR if "ocr" in method else THRESHOLD


CHOICE_TYPES = {FieldType.RADIO, FieldType.CHECKBOX, FieldType.SELECT}

# Built by code from the Standards ("Dane", contact + consent, finish page), never in `pages`.
_STANDARD_SECTION = re.compile(
    r"^(dane )?wnioskodawc|^twoje dane|pełnomocni|^dane kontaktowe|^kontakt|^podpis|adresat|do którego"
    r"|^dane identyfikacyjne( wnioskodawcy| składającego( deklarację)?)?$"
    r"|^adres (zamieszkania|siedziby)( lub [\w ]+| (zamieszkania|siedziby))?( wnioskodawcy| składającego( deklarację)?)?$"
)
_STANDARD_LABEL = re.compile(r"pieczę|klauzul|\brodo\b|pełnomocni|dokument\w* upoważniając|wnioskodaw|^podpis$|^podpis wnioskodawcy")
# "(wypełnić jeżeli nieruchomość nie ma adresu)": a field the paper asks only in some case is case data
_FILL_ONLY_IF = re.compile(r"wypełni\w*,? (tylko|jeżeli|jeśli|w przypadku|gdy|wyłącznie)")
_APPLICANT_WORD = re.compile(r"wnioskodaw")  # the part of _STANDARD_LABEL a choice question may contain (a proxy question may not)
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


@lru_cache(maxsize=16)
def _paper_lines(text: str) -> tuple[str, list[int]]:
    """The normalized paper as one string of its non-empty lines, and the offset where each line starts."""
    lines = [n for n in (normalize(line) for line in text.splitlines()) if n]
    starts, pos = [], 0
    for n in lines:
        starts.append(pos)
        pos += len(n) + 1
    return " ".join(lines), starts


def span_lines(span: tuple[int, int], text: str) -> tuple[int, int]:
    """(first line, last line) of a span of `_paper_lines(text)`'s joined string, counted over the non-empty lines."""
    _, starts = _paper_lines(text)
    a, b = span
    return bisect_right(starts, a) - 1, bisect_right(starts, max(a, b - 1)) - 1


def _exact_spans(q: str, joined: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in re.finditer(rf"(?<!\w){re.escape(q)}(?!\w)", joined)]


def quote_lines(quote: str, text: str) -> list[tuple[int, int]]:
    """Where `quote` sits on the paper: (first line, last line) of every exact occurrence, else of the
    one best fuzzy match, else []. Lines are counted over the non-empty lines of `text`."""
    q = normalize(quote)
    if len(q) < MIN_QUOTE_CHARS:
        return []
    joined, _ = _paper_lines(text)
    hits = _exact_spans(q, joined)
    if hits:
        return [span_lines(h, text) for h in hits]
    al = fuzz.partial_ratio_alignment(q, joined)
    return [span_lines((al.dest_start, al.dest_end), text)] if al and al.score >= THRESHOLD_OCR else []


_NEAR = 6  # lines: how far on a line that is exactly the quote beats a longer line that only contains it
# an ellipsis the model put into a quote ("należy wnieść ... przed złożeniem"); the paper text never has one, its dot
# runs are "____" (`app.extract.normalize`, `prompts/transcribe.md`)
_ELLIPSIS = re.compile(r"\[?\s*(?:\.{3,}|\u2026+)\s*\]?")
_ELLIPSIS_GAP = 300  # chars of the normalized paper allowed between two parts of an elided quote


def _elided_span(quote: str, joined: str, cursor: int) -> tuple[int, int] | None:
    """An elided quote placed by its parts: every part (2+ words) found exactly, in order, each within
    `_ELLIPSIS_GAP` chars after the previous one, the first after the cursor (else the last before it). The span runs from
    the first part's start to the last part's end. None when the quote has no ellipsis or any part is missing."""
    parts = [n for n in (normalize(x) for x in _ELLIPSIS.split(quote)) if n]
    if len(parts) < 2 or any(len(n.split()) < 2 for n in parts):
        return None
    firsts = _exact_spans(parts[0], joined)
    rest = [re.compile(rf"(?<!\w){re.escape(n)}(?!\w)") for n in parts[1:]]
    for base in [cursor, 0] if cursor else [0]:
        # nothing after the cursor: the occurrence nearest before it, as for an exact quote
        for start, end in firsts if base or not cursor else firsts[::-1]:
            if start < base:
                continue
            second = None
            for rx in rest:
                m = rx.search(joined, end)  # the first hit after the previous part is the closest one
                if not m or m.start() > end + _ELLIPSIS_GAP:
                    break
                second = m.start() if second is None else second
                end = m.end()
            else:
                # a first part printed again before the second ("Imię i nazwisko" of two people): its last one
                return max(a for a, b in firsts if start <= a and b <= second), end
    return None


def place_quotes(items: list[tuple[str, str | None, bool]], text: str) -> dict[str, tuple[int, int]]:
    """Where each quote sits on the paper, read in form order: one place per key, as a span of
    `_paper_lines(text)`'s joined string (`span_lines` turns it into lines). The one walk behind both the paper
    headings ("W dokumencie: ...", `app.headings`) and the crops of the paper ("Źródło", `app.sources`), so the two
    never disagree.

    `items` = (key, quote, moves) in form order. A label printed many times ("Miejscowość", "Numer lokalu") resolves
    to its first occurrence after the previous quote (the cursor), else the last one before it; a line that is
    exactly the quote, a few lines on, beats a longer line that only contains it. No exact occurrence: a quote the model
    shortened with an ellipsis by its parts (`_elided_span`), else the best fuzzy match (OCR) after the cursor, else
    anywhere. `moves=False` (a condition's quote) is looked up without moving the
    cursor. Quotes not on paper, or too short to prove anything, get no key."""
    joined, starts = _paper_lines(text)
    out, cursor = {}, 0
    for key, quote, moves in items:
        q = normalize(quote or "")
        if len(q) < MIN_QUOTE_CHARS:
            continue
        hits = _exact_spans(q, joined)
        if hits:
            ahead = [h for h in hits if h[0] >= cursor] or [max(hits)]
            pick, first = ahead[0], span_lines(ahead[0], text)[0]
            for h in ahead:
                a, b = span_lines(h, text)
                if a - first > _NEAR:
                    break
                if a == b and h[0] == starts[a] and h[1] == (starts[a + 1] - 1 if a + 1 < len(starts) else len(joined)):
                    pick = h  # the whole line is the quote
                    break
        else:
            pick = _elided_span(quote, joined, cursor)
            if pick is None:
                for base in [cursor, 0] if cursor else [0]:
                    al = fuzz.partial_ratio_alignment(q, joined[base:])
                    if al and al.score >= THRESHOLD_OCR:
                        pick = (base + al.dest_start, base + al.dest_end)
                        break
            if pick is None:
                continue
        out[key] = pick
        if moves:
            cursor = max(cursor, pick[1])
    return out


def _fix_options(wanted: list[str], options: list[str]) -> list[str]:
    """Map the condition's options onto the trigger's real options (fuzzy), drop unknown."""
    norm = [normalize(o) for o in options]
    fixed = []
    for w in wanted:
        hit = process.extractOne(normalize(w), norm, scorer=fuzz.ratio)
        if hit and hit[1] >= 80 and options[hit[2]] not in fixed:
            fixed.append(options[hit[2]])
    return fixed


# words that state a dependency ("wypełnić, jeżeli ...", "dotyczy tylko ...", "w przypadku ...")
_CONDITIONAL = re.compile(r"\b(jeżeli|jeśli|w przypadku|w razie|gdy|kiedy|tylko|wyłącznie|jedynie|dotyczy|o ile)\b")
_YES_NO = {"tak", "nie"}


def _overlap(a: list[tuple[int, int]], b: list[tuple[int, int]], reach: int = 0) -> bool:
    """Some span of `a` starts inside a span of `b`, or up to `reach` lines after it."""
    return any(y0 <= x0 <= y1 + reach or x0 <= y0 <= x1 for x0, x1 in a for y0, y1 in b)


def _supported(c, trig, target, text: str, next_section: bool | None) -> bool:
    """Is the condition backed by more than the trigger itself? The model likes to hang fields on an
    unrelated question and "prove" it with that question's own line. Supported when the quote
    - has a conditional word, or is some other paper line than the trigger's question / the chosen option;
    - or repeats the trigger, but the target sits on the chosen option's paper line ("[ ] tatuaż ____"),
      for a Tak/Nie trigger up to 2 lines below the question, or (a section) right after the trigger's
      section.
    `next_section`: for a section target, whether it directly follows the trigger's section; else None.
    A quote we cannot place on the paper counts as supported."""
    if _CONDITIONAL.search(unicodedata.normalize("NFKC", c.quote).lower()):
        return True
    q = normalize(c.quote)
    quote_at, trig_at = quote_lines(c.quote, text), quote_lines(trig.quote, text)
    repeats = (
        fuzz.partial_ratio(q, normalize(trig.quote)) >= 90
        or any(fuzz.ratio(q, normalize(o)) >= 90 for o in c.options)
        or bool(quote_at and trig_at and _overlap(quote_at, trig_at))
    )
    if not repeats or not quote_at or next_section:
        return True
    target_at = quote_lines(target.quote if next_section is None else target.fields[0].quote, text)
    if {normalize(o) for o in trig.options} <= _YES_NO:
        return _overlap(target_at, quote_at, reach=2)
    return _overlap(target_at, quote_at + [at for o in c.options for at in quote_lines(o, text)])


def _targets(plan: Plan) -> list[tuple]:
    """(section, element, is_section) for every section and field, in plan order."""
    return [(sec, t, t is sec) for page in plan.pages for sec in page.sections for t in [sec, *sec.fields]]


def _drop_vacuous(plan: Plan, dropped: list[str]) -> None:
    """A negated condition over every option of its trigger hides the target for every answer: clear it. A plain one
    over every option of a required RADIO/SELECT that is always shown holds for every answer ("Opinia klubu" shown
    for each of the three request kinds): clear it too, the target keeps its flags."""
    fields = {f.id: (s, f) for s, f, is_sec in _targets(plan) if not is_sec}
    for _, t, is_sec in _targets(plan):
        c = t.show_if
        home, trig = fields.get(c.field, (None, None)) if c else (None, None)
        if not (c and trig and trig.options and set(_fix_options(c.options, trig.options)) == set(trig.options)):
            continue
        if c.negate:
            dropped.append(f"condition on {t.id}: negated over every option of {c.field}, never shown")
            t.show_if = None
            if not is_sec:
                t.required = False
        elif (trig.type in (FieldType.RADIO, FieldType.SELECT) and trig.required and trig.show_if is None
              and home.show_if is None and not home.repeating):
            dropped.append(f"condition on {t.id}: every option of {c.field}, always shown")
            t.show_if = None


def _hoist_triggers(plan: Plan, ok, dropped: list[str]) -> None:
    """A trigger that comes after its target ("E.3 amount" shown by "Czy dołączasz załącznik A?" on the last page)
    moves right before the target, instead of the condition being dropped: before the target field in its section,
    or for a section target to the end of the section before it on the same page (or a new section there titled like
    the trigger's old one). Only an unconditional choice field with options outside repeating sections moves, and only
    for a condition whose quote is on paper and whose options exist; sections left empty go."""
    for _ in range(50):  # one move per round, the order changes
        order = {t.id: n for n, (_, t, _) in enumerate(_targets(plan))}
        home = {f.id: sec for sec, f, is_sec in _targets(plan) if not is_sec}
        move = None
        for sec, t, is_sec in _targets(plan):
            c = t.show_if
            if c is None or c.field not in home or order[c.field] < order[t.id]:
                continue
            src = home[c.field]
            trig = next(f for f in src.fields if f.id == c.field)
            if (trig.type in CHOICE_TYPES and trig.options and trig.show_if is None and not src.repeating
                    and not sec.repeating and not (is_sec and src is sec) and ok(c.quote)
                    and _fix_options(c.options, trig.options)):
                move = (sec, t, is_sec, src, trig)
                break
        if move is None:
            return
        sec, t, is_sec, src, trig = move
        src.fields.remove(trig)
        page = next(p for p in plan.pages if any(s is sec for s in p.sections))
        if not is_sec:
            sec.fields.insert(next(i for i, f in enumerate(sec.fields) if f is t), trig)
        else:
            i = next(i for i, s in enumerate(page.sections) if s is sec)
            prev = page.sections[i - 1] if i else None
            if prev and prev is not src and not prev.repeating and not prev.address and prev.show_if is None:
                prev.fields.append(trig)
            else:
                page.sections.insert(i, Section(id=f"s{trig.id}", title=src.title, fields=[trig]))
        dropped.append(f"hoist: trigger {trig.id} '{trig.label}' moved before its target {t.id}")
        for p in plan.pages:
            p.sections = [s for s in p.sections if s.fields]
        plan.pages = [p for p in plan.pages if p.sections]


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
                # a choice field inside an applicant section is usually case data ("Składający jako: ..."),
                # so are attachments ("Kontakt i załączniki") and fields asked only in some case
                in_std_sec = (std_sec and f.type not in CHOICE_TYPES and f.type != FieldType.FILE_UPLOADER
                              and not _FILL_ONLY_IF.search(f.quote.lower()))
                # the same for a question about the applicant ("Czy osoba odpowiedzialna to wnioskodawca?")
                std = _STANDARD_LABEL.search(lab)
                std_label = std and not (f.type in CHOICE_TYPES and _APPLICANT_WORD.fullmatch(std.group()))
                if in_std_sec or std_label or _STANDARD_QUOTE.search(quo) or _FILL_DATE.search(quo):
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
    from app.repair import repair_plan  # repair imports this module

    _drop_vacuous(plan, dropped)
    dropped += repair_plan(plan, text)
    if not plan.pages:
        raise ValueError("no page of the plan is backed by the paper text")
    _hoist_triggers(plan, ok, dropped)

    # 2. Conditions. Order = position in the plan; a trigger must come before its target,
    # which also rules out cycles. A trigger that is itself conditional is fine (chain).
    order: dict[str, int] = {}
    sec_no: dict[str, int] = {}  # section or field id -> number of its section
    fields = {}
    for n, sec in enumerate(s for page in plan.pages for s in page.sections):
        order[sec.id] = len(order)
        sec_no[sec.id] = n
        for f in sec.fields:
            order[f.id] = len(order)
            sec_no[f.id] = n
            fields[f.id] = f

    def check(target_id: str, target, own_fields: set[str], is_section: bool = False) -> None:
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
                if not _supported(c, trig, target, text, sec_no[target_id] - sec_no[c.field] == 1 if is_section else None):
                    why = f"quote only repeats the trigger {c.field}, the target is not on the chosen option's line: {c.quote!r}"
            else:
                why = f"options {c.options} not among {trig.options}"
        if why:
            dropped.append(f"condition on {target_id}: {why}")
            target.show_if = None
            if not is_section:  # hidden until now: do not make it a must for everyone (a section keeps its flags)
                target.required = False

    for page in plan.pages:
        for sec in page.sections:
            check(sec.id, sec, {f.id for f in sec.fields}, is_section=True)
            for f in sec.fields:
                check(f.id, f, set())

    # 3. A question the conditions pass added that no condition uses any more changes nothing: drop it.
    used = {t.show_if.field for _, t, _ in _targets(plan) if t.show_if}
    for page in plan.pages:
        for sec in page.sections:
            dropped += [f"question {f.id} '{f.label}': added for a condition that is gone"
                        for f in sec.fields if f.synthesized and f.id not in used]
            sec.fields = [f for f in sec.fields if not f.synthesized or f.id in used]
        page.sections = [s for s in page.sections if s.fields]
    plan.pages = [p for p in plan.pages if p.sections]

    # 4. A field shown by its own condition is required when shown (`app.repair.require_shown`).
    from app.repair import require_shown

    dropped += require_shown(plan)
    return plan, dropped
