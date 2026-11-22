"""New dictionary proposals (bonus II): next to the COI JSON, never inside it.

The COI JSON stays clean and importable: a choice field keeps its own `options`. This
module only says "this list deserves a shared COI dictionary": name, structure
(kod + nazwa), values and the field it fits. The upload page shows it in the
"Proponowane słowniki" panel and offers it as a separate JSON download.

    from app.dictionaries import propose
    result = propose(data, filename)   # {"source", "proposals": [...], "dropped": [...]}

The model proposes (`app.llm.dictionaries`), code decides (`filter_candidates`). A
candidate is dropped when its field quote is not on paper, it has values but fewer than
2 real ones, it is a yes/no pair (Tak/Nie, jest/brak, "X"/"nie X"), an existing COI dictionary or the Standards address block covers it, any value is a whole sentence, or any of its
values is missing from the paper text (same matcher as `app.quotes`). A candidate
without values is a blank closed-set field ("Rasa psa: ____"): kept with
"wartości do uzupełnienia przez urząd/COI", unless its label is plainly free text.
"""

import re
import unicodedata

from app.llm.dictionaries import ask_candidates
from app.llm.transcribe import transcribe
from app.quotes import THRESHOLD, normalize, quote_found

TO_FILL = "Wartości do uzupełnienia przez urząd/COI."
FROM_FORM = "Wartości przepisane z formularza."
STRUCTURE = [
    {"name": "kod", "type": "string", "description": "Stały identyfikator pozycji"},
    {"name": "nazwa", "type": "string", "description": "Nazwa pozycji wyświetlana użytkownikowi"},
]

# Dictionaries COI already has (Słowniki doc): address TERYT, countries, phone prefixes. The rest of an
# address (street, postal code, post office) comes with the Standards address block, never a new dictionary.
_EXISTING = re.compile(
    r"\bwojewódz|\bpowiat(y|ów|u)?\b|\bgmin(a|y)\b|\bmiejscowoś|\bkraj(e|ów|u)?\b|\bpaństw(a|o)?\b"
    r"|obywatelstw|kierunkow|prefiks|\bteryt\b|\bulic(a|e|y)?\b|kod\w* pocztow"
)
# One-value-or-the-other pairs that are a yes/no question in disguise.
_YES_NO = {"tak", "nie", "jest", "brak", "posiadam", "nie posiadam", "dotyczy", "nie dotyczy", "wynika", "nie wynika"}
# Longer values (not counting "(...)" explanations) are sentences, statements or clauses, not dictionary items.
MAX_VALUE_CHARS = 120
_PAREN = re.compile(r"\([^()]*\)")
# Catch-all options; a list needs 2 real values besides them.
_CATCH_ALL = re.compile(r"^(inn[aeyi]|inne|pozostał[aey]|other)\b")
# Blank fields whose answer is free text, never a closed set.
_FREE_TEXT = re.compile(
    r"\bimi[eę]\b|nazwisk|\bopis|uzasadnien|\buwagi\b|podpis|\badres|e-?mail|telefon|\bpesel\b|\bnip\b|\bregon\b"
    r"|\bdata\b|\bkwot|\bcel\b|nazwa (firmy|klubu|przedsiębior|podmiotu)|osiągnię"
)


def _negated_pair(values: set[str]) -> bool:
    """{"są kompostowane", "nie są kompostowane"}: a yes/no question worded as two options."""
    if len(values) != 2:
        return False
    a, b = sorted(values, key=len)
    return b == f"nie {a}"


def _value_on_paper(value: str, text: str, threshold: float) -> bool:
    """Short values ("A", "Ls") need an exact word; longer ones use the quote matcher."""
    v = normalize(value)
    if not v:
        return False
    if len(v) < 5:
        return re.search(rf"(?<!\w){re.escape(v)}(?!\w)", normalize(text)) is not None
    return quote_found(value, text, threshold)


def _code(name: str, taken: set[str]) -> str:
    """Item code: ASCII upper slug of the name, unique within the dictionary ("Piłka nożna" -> "PILKA_NOZNA")."""
    ascii_ = unicodedata.normalize("NFKD", name.replace("ł", "l").replace("Ł", "L")).encode("ascii", "ignore").decode()
    base = re.sub(r"[^A-Za-z0-9]+", "_", ascii_).strip("_").upper()[:40] or "POZYCJA"
    code, n = base, 2
    while code in taken:
        code, n = f"{base}_{n}", n + 1
    taken.add(code)
    return code


def _clean(s) -> str:
    return re.sub(r"\s+", " ", s).strip() if isinstance(s, str) else ""


def filter_candidates(candidates: list[dict], text: str, threshold: float = THRESHOLD) -> tuple[list[dict], list[str]]:
    """Model candidates -> (proposals ready to show and download, human-readable notes for every drop)."""
    kept: list[dict] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for c in candidates:
        name, field, quote, reason = (_clean(c.get(k)) for k in ("name", "field", "quote", "reason"))
        values: list[str] = []
        for v in c.get("values") or []:
            v = _clean(v)
            if v and normalize(v) not in {normalize(x) for x in values}:
                values.append(v)
        label = f"'{name}' ({field})"
        norm_values = {normalize(v) for v in values}
        real = [v for v in values if not _CATCH_ALL.search(normalize(v))]
        why = None
        if not name or not field:
            why = "brak nazwy lub pola"
        elif not quote_found(quote, text, threshold):
            why = f"pola nie ma na formularzu: {quote!r}"
        elif _EXISTING.search(normalize(f"{name} {field}")):
            why = "pokrywa go istniejący słownik COI albo blok adresu ze Standardów (adresowe, Kraje, Numery kierunkowe)"
        elif values and (norm_values <= _YES_NO or _negated_pair(norm_values)):
            why = "to pytanie Tak/Nie"
        elif values and len(real) < 2:
            why = "lista ma mniej niż 2 wartości"
        elif any(len(_PAREN.sub("", v).strip()) > MAX_VALUE_CHARS for v in values):
            why = "wartości to całe zdania (oświadczenia), nie pozycje słownika"
        elif missing := [v for v in values if not _value_on_paper(v, text, threshold)]:
            why = f"wartości nie ma na formularzu: {missing[:3]}"
        elif not values and _FREE_TEXT.search(normalize(field)):
            why = "puste pole z dowolną treścią, nie zamknięty zbiór"
        elif normalize(name) in seen or normalize(quote) in seen:
            why = "duplikat"
        if why:
            dropped.append(f"{label}: {why}")
            continue
        seen.update({normalize(name), normalize(quote)})
        taken: set[str] = set()
        kept.append({
            "name": name,
            "code": _code(name, set()),
            "reason": reason,
            "field": {"label": field, "quote": quote, "types": ["SELECT", "RADIO", "CHECKBOX"]},
            "structure": STRUCTURE,
            "status": "z_formularza" if values else "do_uzupelnienia",
            "note": FROM_FORM if values else TO_FILL,
            "items": [{"kod": _code(v, taken), "nazwa": v} for v in values],
        })
    return kept, dropped


def propose(data: bytes, filename: str) -> dict:
    """Bytes of one blank form -> dictionary proposals, read like the main pipeline (page images -> Markdown,
    `app.llm.transcribe`). Raises like the main pipeline on unreadable input."""
    doc = transcribe(data, filename)
    proposals, dropped = filter_candidates(ask_candidates(doc.text), doc.text)
    return {"source": filename, "proposals": proposals, "dropped": dropped}
