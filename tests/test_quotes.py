"""Quote filter: drops what the paper does not back, fixes or drops broken conditions."""

import pytest

from app.plan import Condition, FieldType, Page, Plan, PlanField, Section, Statement
from app.quotes import filter_plan, normalize, quote_found

TEXT = """Wnioskodawca:
................................................................
          Nazwisko i imię/Nazwa firmy
Kwotę nadpłaty proszę:
1) zaliczyć na poczet przyszłego zobowiązania podatkowego z tytułu ……….……………
2) zwrócić przelewem na wskazany rachunek
w załączeniu:
1) korekta deklaracji
Oświadczam, że dane są prawdziwe.
"""


def f(id, label, quote, type=FieldType.TEXT_FIELD, options=(), show_if=None):
    return PlanField(id=id, label=label, type=type, options=list(options), quote=quote, show_if=show_if)


def plan(fields, statements=()):
    return Plan(title="Wniosek", addressee="Urząd", pages=[Page(title="P", sections=[Section(id="s1", title="S", fields=fields)])],
                statements=list(statements))


RADIO = f("f1", "Co zrobić z nadpłatą", "Kwotę nadpłaty proszę:", FieldType.RADIO, ["Zaliczyć", "Zwrócić przelewem"])


def test_normalize_strips_placeholders_case_and_space():
    assert normalize("Z tytułu ……….……………  ____ X") == "z tytułu x"
    assert normalize("ulicy1)") == "ulicy"


def test_quote_found_exact_fuzzy_and_missing():
    assert quote_found("zaliczyć na poczet przyszłego zobowiązania podatkowego z tytułu ____", TEXT)
    assert quote_found("zwrocić przelewem na wskazany rachunck", TEXT)  # OCR-like typos pass
    assert not quote_found("numer konta bankowego odbiorcy", TEXT)
    assert not quote_found("____", TEXT)  # placeholder alone proves nothing


def test_drops_field_and_statement_without_quote_keeps_rest():
    p = plan([RADIO, f("f2", "Kolor oczu", "kolor oczu wnioskodawcy")],
             [Statement(text="Dane są prawdziwe", quote="Oświadczam, że dane są prawdziwe."),
              Statement(text="Zmyślone", quote="Oświadczam, że mam psa.")])
    out, dropped = filter_plan(p, TEXT)
    assert [x.id for x in out.pages[0].sections[0].fields] == ["f1"]
    assert len(out.statements) == 1
    assert len(dropped) == 2


def test_empty_sections_and_pages_go_and_all_gone_raises():
    with pytest.raises(ValueError):
        filter_plan(plan([f("f1", "X", "nie ma tego w tekście wcale")]), TEXT)


def test_condition_options_fixed_to_trigger_options():
    cond = Condition(field="f1", options=["zwrócić przelewem"], quote="zwrócić przelewem na wskazany rachunek")
    out, dropped = filter_plan(plan([RADIO, f("f2", "Numer rachunku", "wskazany rachunek", show_if=cond)]), TEXT)
    assert out.pages[0].sections[0].fields[1].show_if.options == ["Zwrócić przelewem"]
    assert dropped == []


@pytest.mark.parametrize(
    "cond_field,options,quote,why",
    [
        ("f9", ["Zaliczyć"], "Kwotę nadpłaty proszę:", "does not exist"),
        ("f3", ["Zaliczyć"], "Kwotę nadpłaty proszę:", "not a choice field"),
        ("f1", ["Psy"], "Kwotę nadpłaty proszę:", "not among"),
        ("f1", ["Zaliczyć"], "zmyślone zdanie o warunku", "quote not in text"),
    ],
)
def test_broken_condition_dropped_target_kept(cond_field, options, quote, why):
    cond = Condition(field=cond_field, options=options, quote=quote)
    p = plan([RADIO, f("f2", "Numer rachunku", "wskazany rachunek", show_if=cond), f("f3", "Korekta", "korekta deklaracji")])
    out, dropped = filter_plan(p, TEXT)
    assert out.pages[0].sections[0].fields[1].show_if is None
    assert len(dropped) == 1 and why in dropped[0]


def test_trigger_after_target_dropped():
    cond = Condition(field="f2", options=["Zaliczyć"], quote="Kwotę nadpłaty proszę:")
    radio = RADIO.model_copy(update={"id": "f2"})
    out, dropped = filter_plan(plan([f("f1", "Korekta", "korekta deklaracji", show_if=cond), radio]), TEXT)
    assert out.pages[0].sections[0].fields[0].show_if is None
    assert "after the target" in dropped[0]


def test_section_condition_on_own_field_dropped():
    cond = Condition(field="f1", options=["Zaliczyć"], quote="Kwotę nadpłaty proszę:")
    p = plan([RADIO])
    p.pages[0].sections[0].show_if = cond
    out, dropped = filter_plan(p, TEXT)
    assert out.pages[0].sections[0].show_if is None
    assert "inside its own target" in dropped[0]


def test_fields_of_generated_pages_dropped():
    p = plan([RADIO, f("f2", "Imię i nazwisko wnioskodawcy", "Nazwisko i imię/Nazwa firmy"),
              f("f3", "Podpis", "Oświadczam, że dane są prawdziwe.")])
    p.pages[0].sections.append(Section(id="s2", title="Dane wnioskodawcy", fields=[f("f4", "Nazwa firmy", "Nazwa firmy")]))
    out, dropped = filter_plan(p, TEXT)
    assert [x.id for s in out.pages[0].sections for x in s.fields] == ["f1"]
    assert len(dropped) == 3 and all(d.startswith("standard:") for d in dropped)


@pytest.mark.parametrize(
    "quote,text",
    [
        ("5. Nazwisko, imię", "5. | Nazwisko, imię"),  # ODS/XLSX cells
        ("50. Wysokość", "Wysokość miesięcznej opłaty"),  # OCR lost the numbering
        ("B.1 DANE IDENTYFIKACYJNE", "B.1.\tDane identyfikacyjne"),
        ("tel.", "tel ____"),  # extractor dropped the dot
        ("a/ jezdnia", "a) jezdnia (przy zajęciu do 20% szerokości)"),
        ("Rasa psa:", "Rasa\n❑ pies ❑ suka\npsa: ____"),  # label split across OCR columns
    ],
)
def test_quote_robust_across_formats(quote, text):
    assert quote_found(quote, text)
