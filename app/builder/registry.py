"""Bonus I on the subject-matter pages: which Plan fields the PESEL registers may feed.

Code decides from the label and its section, never the model, and only when the field is
plainly about the applicant. Both checks are allow-lists: the label is exactly a registry
attribute ("Data urodzenia", optionally "wnioskodawcy" / "Twoja"), and the applicant is named
by the label itself or by a section title that is exactly an applicant heading ("Dane
wnioskodawcy", "Twoje dane"...). The page title never counts. A registry field is read-only
("Dane inicjalne"), so the citizen cannot fix a wrong link: a repeating section, any word about
another person or a second applicant anywhere around it means no link.
Forms that change the registration or PESEL data are skipped (the register holds the old value).
Usage: `pesel_links(plan, page_titles)`, `address_source(section, page_title, plan_title)`; the
builder wires the results.
"""

import re
from collections import Counter

from app.plan import FieldType, Plan, PlanField, Section
from app.text import ascii_fold

# Label core (ascii-folded, lowercase, applicant words stripped) -> (attribute, allowed plan type).
# Applicant's name, surname and PESEL are not here: the "Dane" page already has them (MLPZ).
# Sex and marital status are choices on paper, the register gives text: not linked.
# "Narodowość" is not here: on paper it often means ethnicity, not citizenship.
_PESEL = {
    "data urodzenia": ("PESEL.birthDate", FieldType.DATEPICKER),
    "miejsce urodzenia": ("PESEL.birthPlace", FieldType.TEXT_FIELD),
    "kraj urodzenia": ("PESEL.birthCountry", FieldType.TEXT_FIELD),
    "obywatelstwo": ("PESEL.citizenship", FieldType.TEXT_FIELD),
    "nazwisko rodowe": ("PESEL.familyName", FieldType.TEXT_FIELD),
    "drugie imie": ("PESEL.secondName", FieldType.TEXT_FIELD),
    "imie ojca": ("PESEL.fatherName", FieldType.TEXT_FIELD),
    "imie matki": ("PESEL.motherName", FieldType.TEXT_FIELD),
}

# "Dane zameldowania", permanent address only (the temporary one is empty for most people, and an
# empty read-only field cannot be filled). No country: a registered address is always in Poland.
_ADDRESS_KEYS = {"place": "Place", "postCode": "PostalCode", "street": "Street", "voivodeship": "Voivodeship",
                 "building": "HouseNumber", "apartment": "ApartmentNumber"}
PERMANENT_ADDRESS = {k: f"PESEL_ADDRESS.permanentAddress{v}" for k, v in _ADDRESS_KEYS.items()}

_LEAD = re.compile(r"^twoj\w*\s+")
_TAIL = re.compile(r"\s+(wnioskodawcy|osoby wnioskujacej|skladajacego(\s+(wniosek|deklaracje))?)$")
# Section titles that are the applicant and nobody else (folded, a trailing "(...)" or ":" dropped).
_SELF_SECTION = re.compile(
    r"twoje dane( osobowe)?|wnioskodawca|dane( osobowe)? wnioskodawcy|dane osoby wnioskujacej"
    r"|(dane )?(osoby )?skladajace(go|j)( wniosek| deklaracje)?|skladajacy( wniosek| deklaracje)?"
)
_SELF_ADDRESS = re.compile(r"(twoj )?adres (stalego )?zameldowania( na pobyt staly)?( wnioskodawcy)?")
_OTHER = re.compile(
    r"dzie(c|ck)|malzon|pelnomocn|wlasciciel|odpowiedzialn|\bczlon|kandydat|mocodawc|opiekun|\brodzic"
    r"|\bmat(k|ce)|\boj(c|ciec)|zmarl|\bchor|\bucz(en|ni)|zawodnik|uczestnik|najem|dzierzaw|spadk"
    r"|\b(pies|psa|psu|psie|psem|psow)\b|zwierz|\binn\w* (niz )?osob|\bosob\w*,? ktor\w*( \w+)? dotycz"
    r"|poszkodowan|maloletn|nieletn|podopieczn|\bswiad(ek|k)|kierownik|wykonawc|przedstawiciel"
    r"|reprezentant|reprezentowan|upowazn|uprawnion|zarzadc|posiadacz|lekarz|pacjent|gospodarstw"
    r"|\brodzin|partner|wnuk|\brodzenstw|\bbrat|siostr|\bsyn(a|em|owi|u|ow)?\b|\bcor(k|ce)"
    r"|\bzon(a|e|y|ie)\b|\bm(az|eza|ezem|ezowi|ezu)\b|nabywc|zbywc|sprzedawc|inwestor|dluzn"
    r"|wierzyciel|pracownik|pracodawc|lokator|najemc|uzytkownik|\bw imieniu\b|\binna niz\b|nie jest wnioskodawc"
    r"|wnucz|dziad|babc|tesci?|ziec|zieci|krewn|student|zainteresowan|kontaktow|ubezpieczon|niepelnospraw"
    r"|cudzoziem|swiadczeniobiorc|zglasza(n|sz)|\bosob\w*\b(?! (wnioskujac|skladajac))"
)
_SECOND = re.compile(r"wspol|\bdrug(i|a|iego|iej)\b|kolejn|pozostal|\bnr\b|\d|wnioskodawcow")
# The form changes the registration or the PESEL data itself: the register holds the old value.
_CHANGE_FORM = re.compile(r"meldow|meldun|pobyt|zmian\w* adres|przeprowadz|pesel|sprostow|transkrypc|uzupelnien\w* akt")


def _fold(*parts: str) -> str:
    return " ".join(ascii_fold(p).lower().strip() for p in parts if p)


def _heading(title: str) -> str:
    return re.sub(r"(\s*(\([^)]*\)|:))+\s*$", "", _fold(title))


def _core(label: str) -> str:
    return _TAIL.sub("", _LEAD.sub("", _fold(label).rstrip(":").strip()))


def pesel_attribute(f: PlanField, s: Section, page_title: str, plan_title: str) -> str | None:
    """`PESEL.<attr>` when field `f` (in section `s` on page `page_title`) is the applicant's own
    registry value, else None. Use `pesel_links` for a whole plan (it also drops duplicates)."""
    if s.repeating or _CHANGE_FORM.search(_fold(plan_title)):
        return None
    label = _fold(f.label).rstrip(":").strip()
    core = _core(f.label)
    hit = _PESEL.get(core)
    if hit is None or f.type != hit[1]:
        return None
    if core == label and not _SELF_SECTION.fullmatch(_heading(s.title)):
        return None  # neither the label nor the section names the applicant
    rest = label.replace(core, " ")  # "imie ojca" itself must not trip the other-person check
    if _OTHER.search(_fold(rest, f.description, s.title, s.description, page_title)):
        return None
    if _SECOND.search(_fold(rest, s.title, page_title)):
        return None
    # a section that also asks about someone else ("Imię małżonka") mixes two people
    others = [_fold(g.label) for g in s.fields if g is not f and _core(g.label) not in _PESEL]
    if any(_OTHER.search(o) or _SECOND.search(o) for o in others):
        return None
    return hit[0]


def pesel_links(plan: Plan, page_titles: list[str]) -> dict[int, str]:
    """`id(field)` -> `PESEL.<attr>` for every field of `plan` that qualifies. An attribute found
    on two fields is linked on neither: one of them may be another person's (a flattened table)."""
    found = {id(f): a for title, page in zip(page_titles, plan.pages) for s in page.sections for f in s.fields
             if (a := pesel_attribute(f, s, title, plan.title))}
    counts = Counter(found.values())
    return {k: a for k, a in found.items() if counts[a] == 1}


def address_source(s: Section, page_title: str, plan_title: str) -> dict[str, str] | None:
    """Attribute map for an address section that is exactly the applicant's permanent registered
    address ("Adres zameldowania wnioskodawcy", "Twój adres stałego zameldowania"), else None."""
    if not s.address or s.repeating or _CHANGE_FORM.search(_fold(plan_title, page_title)):
        return None
    title = _heading(s.title)
    if not _SELF_ADDRESS.fullmatch(title) or not re.search(r"^twoj|wnioskodawcy$", title):
        return None
    context = _fold(s.title, s.description, page_title, *(f.label for f in s.fields))
    if _OTHER.search(context) or _SECOND.search(_fold(s.title, page_title)) or "czasow" in _fold(s.title):
        return None  # the full title too: a trailing "(...)" was cut only for the allow-list match
    return PERMANENT_ADDRESS
