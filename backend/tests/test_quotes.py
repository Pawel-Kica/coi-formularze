"""Quote filter: drops what the paper does not back, fixes or drops broken conditions."""

import pytest

from app.core.plan import (
    Condition,
    FieldType,
    Page,
    Plan,
    PlanField,
    Section,
    Statement,
)
from app.grounding.quotes import (
    _elided_span,
    _paper_lines,
    filter_plan,
    normalize,
    place_quotes,
    quote_found,
    span_lines,
)

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
    return Plan(title="Wniosek", addressee="", pages=[Page(title="P", sections=[Section(id="s1", title="S", fields=fields)])],
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


def test_late_trigger_moves_before_its_target():
    cond = Condition(field="f2", options=["Zaliczyć"], quote="Kwotę nadpłaty proszę:")
    radio = RADIO.model_copy(update={"id": "f2"})
    target = f("f1", "Z tytułu", "zobowiązania podatkowego z tytułu", show_if=cond)  # on the chosen option's line
    out, dropped = filter_plan(plan([target, radio]), TEXT)
    assert [x.id for x in out.pages[0].sections[0].fields] == ["f2", "f1"]
    assert out.pages[0].sections[0].fields[1].show_if.field == "f2"
    assert any(d.startswith("hoist: trigger f2") for d in dropped)


def test_late_trigger_of_a_section_moves_into_the_section_before_or_its_own():
    cond = Condition(field="f2", options=["Zaliczyć"], quote="Kwotę nadpłaty proszę:")
    radio = RADIO.model_copy(update={"id": "f2"})
    target = Section(id="s2", title="T", fields=[f("f1", "Korekta", "korekta deklaracji")], show_if=cond)
    p = Plan(title="Wniosek", addressee="", pages=[
        Page(title="A", sections=[target]),
        Page(title="B", sections=[Section(id="s3", title="Załączniki", fields=[radio])])])
    out, _ = filter_plan(p, TEXT)
    assert [s.id for s in out.pages[0].sections] == ["sf2", "s2"] and len(out.pages) == 1  # page B left empty
    assert out.pages[0].sections[0].title == "Załączniki" and out.pages[0].sections[1].show_if.field == "f2"


def test_late_trigger_that_is_conditional_itself_is_not_moved():
    cond = Condition(field="f2", options=["Zaliczyć"], quote="Kwotę nadpłaty proszę:")
    own = Condition(field="f3", options=["Tak"], quote="Kwotę nadpłaty proszę:")
    radio = RADIO.model_copy(update={"id": "f2", "show_if": own})
    yes_no = f("f3", "Czy nadpłata?", "Kwotę nadpłaty proszę:", FieldType.RADIO, ["Tak", "Nie"])
    out, dropped = filter_plan(plan([f("f1", "Korekta", "korekta deklaracji", show_if=cond), radio, yes_no]), TEXT)
    assert out.pages[0].sections[0].fields[0].show_if is None
    assert any("after the target" in d for d in dropped)


def test_negated_condition_over_every_option_cleared():
    cond = Condition(field="f1", options=["Zaliczyć", "Zwrócić przelewem"], negate=True, quote="Kwotę nadpłaty proszę:")
    target = f("f2", "Korekta", "korekta deklaracji", show_if=cond).model_copy(update={"required": True})
    out, dropped = filter_plan(plan([RADIO, target]), TEXT)
    kept = out.pages[0].sections[0].fields[1]
    assert kept.show_if is None and not kept.required
    assert "negated over every option of f1" in dropped[0]


def test_vacuous_gate_cleared_before_the_correspondence_repair():
    text = TEXT + "B.4 ADRES DO KORESPONDENCJI (wypełnić tylko w przypadku kiedy jest inny niż w poz. B.3)\nMiejscowość\n"
    cond = Condition(field="f1", options=["Zaliczyć", "Zwrócić przelewem"], negate=True,
                     quote="wypełnić tylko w przypadku kiedy jest inny niż w poz. B.3")
    p = plan([RADIO])
    p.pages[0].sections.append(Section(id="s2", title="Adres do korespondencji", show_if=cond,
                                       fields=[f("f2", "Miejscowość", "Miejscowość")]))
    out, _ = filter_plan(p, text)
    gate = next(s for s in out.pages[0].sections if s.id == "s2").show_if
    assert gate and not gate.negate and gate.options == ["Tak"]  # the repair's own Tak/Nie question


def test_section_condition_on_own_field_dropped():
    cond = Condition(field="f1", options=["Zaliczyć"], quote="Kwotę nadpłaty proszę:")
    p = plan([RADIO])
    p.pages[0].sections[0].show_if = cond
    out, dropped = filter_plan(p, TEXT)
    assert out.pages[0].sections[0].show_if is None
    assert "inside its own target" in dropped[0]


def test_added_question_without_a_condition_dropped():
    q = f("f9", "Czy jesteś osobą niepełnoletnią?", "Kwotę nadpłaty proszę:", FieldType.RADIO, ["Tak", "Nie"])
    q.synthesized = True
    used = q.model_copy(update={"id": "f8", "label": "Czy chcesz złożyć korektę?"})
    cond = Condition(field="f8", options=["Tak"], quote="w załączeniu: 1) korekta deklaracji")  # its own paper line
    out, dropped = filter_plan(plan([RADIO, q, used, f("f2", "Korekta", "korekta deklaracji", show_if=cond)]), TEXT)
    assert [x.id for x in out.pages[0].sections[0].fields] == ["f1", "f8", "f2"]
    assert any("question f9" in d for d in dropped)


def test_fields_of_generated_pages_dropped():
    p = plan([RADIO, f("f2", "Imię i nazwisko wnioskodawcy", "Nazwisko i imię/Nazwa firmy"),
              f("f3", "Podpis", "Oświadczam, że dane są prawdziwe.")])
    p.pages[0].sections.append(Section(id="s2", title="Dane wnioskodawcy", fields=[f("f4", "Nazwa firmy", "Nazwa firmy")]))
    out, dropped = filter_plan(p, TEXT)
    assert [x.id for s in out.pages[0].sections for x in s.fields] == ["f1"]
    assert len(dropped) == 3 and all(d.startswith("standard:") for d in dropped)


def test_proxy_question_dropped_even_as_choice_field():
    """Proxy data is on the generated "Dane" page (applicant.proxy), also as a Yes/No question."""
    p = plan([RADIO, f("f2", "Czy w sprawie występuje pełnomocnik?", "Kwotę nadpłaty proszę:", FieldType.RADIO,
                       ["Tak", "Nie"])])
    out, dropped = filter_plan(p, TEXT)
    assert [x.id for s in out.pages[0].sections for x in s.fields] == ["f1"]
    assert dropped[0].startswith("standard:")


def test_other_person_section_kept():
    """Spouse / other person data is case content even when titled like the applicant block."""
    p = plan([RADIO])
    other = f("f2", "Nazwisko", "Nazwisko i imię/Nazwa firmy")
    p.pages[0].sections.append(Section(id="s2", title="Dane identyfikacyjne współmałżonka", fields=[other]))
    p.pages[0].sections.append(Section(id="s3", title="Dane identyfikacyjne", fields=[other.model_copy(update={"id": "f3"})]))
    out, _ = filter_plan(p, TEXT)
    assert [x.id for s in out.pages[0].sections for x in s.fields] == ["f1", "f2"]


@pytest.mark.parametrize(
    "quote,text",
    [
        ("5. Nazwisko, imię", "5. | Nazwisko, imię"),  # ODS/XLSX cells
        ("50. Wysokość", "Wysokość miesięcznej opłaty"),  # OCR lost the numbering
        ("B.1 DANE IDENTYFIKACYJNE", "B.1.\tDane identyfikacyjne"),
        ("tel.", "tel ____"),  # extractor dropped the dot
        ("a/ jezdnia", "a) jezdnia (przy zajęciu do 20% szerokości)"),
        ("Rasa psa:", "Rasa\n❑ pies ❑ suka\npsa: ____"),  # label split across OCR columns
        ("o powierzchni do 45 m²", "o powierzchni do 45 m2 X"),  # superscript vs plain digit
        ("ﬁrma", "firma"),  # ligature in a PDF text layer
    ],
)
def test_quote_robust_across_formats(quote, text):
    assert quote_found(quote, text)


def test_addressee_kept_only_when_its_quote_is_on_paper():
    p = plan([RADIO]).model_copy(update={"addressee": "Urząd Skarbowy", "addressee_quote": "Wnioskodawca:"})
    kept, dropped = filter_plan(p, TEXT)
    assert kept.addressee == "Urząd Skarbowy" and dropped == []
    for quote in ("", "Do Wójta Gminy Testowo"):  # no quote, or a quote the paper lacks: guessed
        cleared, dropped = filter_plan(p.model_copy(update={"addressee_quote": quote}), TEXT)
        assert cleared.addressee == cleared.addressee_quote == "" and len(dropped) == 1


def test_applicant_address_section_dropped_attachments_and_case_fields_stay():
    text = TEXT + ("B.3 ADRES ZAMIESZKANIA / ADRES SIEDZIBY\nMiejscowość\n"
                   "16. Numer działki (wypełnić jeżeli nieruchomość nie ma urzędowo nadanego adresu)\n"
                   "G. ZAŁĄCZNIKI\nZałącznik A\nDokumenty upoważniające do reprezentowania\n")
    p = plan([RADIO])
    p.pages[0].sections.append(Section(id="s2", title="Adres zamieszkania / siedziby", fields=[
        f("f2", "Miejscowość", "Miejscowość"),
        f("f3", "Numer działki", "Numer działki (wypełnić jeżeli nieruchomość nie ma urzędowo nadanego adresu)")]))
    p.pages.append(Page(title="Kontakt i załączniki", sections=[Section(id="s3", title="Załączniki", fields=[
        f("f4", "Plik z załącznikiem A", "Załącznik A", FieldType.FILE_UPLOADER),
        f("f5", "Plik z dokumentami upoważniającymi", "Dokumenty upoważniające do reprezentowania",
          FieldType.FILE_UPLOADER)])]))
    out, dropped = filter_plan(p, text)
    kept = [x.id for pg in out.pages for s in pg.sections for x in s.fields]
    assert kept == ["f1", "f3", "f4"]  # f2: the Dane page has the address; f5: the proxy section has the file
    assert sum(d.startswith("standard:") for d in dropped) == 2


FEE = """Opłata za wydanie zaświadczenia wynosi 17 zł.
Opłatę skarbową należy wnieść w kasie lub bezgotówkowo na rachunek tut. Urzędu – przed złożeniem wniosku.
Konto bankowe Urzędu: Bank PKO BP S.A.
Wniosek należy złożyć przed złożeniem wniosku o dowód.
"""


@pytest.mark.parametrize("quote", ["Opłatę skarbową należy wnieść ... przed złożeniem wniosku",
                                   "Opłatę skarbową należy wnieść … przed złożeniem wniosku",
                                   "Opłatę skarbową należy wnieść [...] przed złożeniem wniosku"])
def test_elided_quote_placed_from_first_to_last_part(quote):
    # the model shortened a long sentence with an ellipsis (form 3 on Eden): every part is on paper, in order
    span = place_quotes([("k", quote, True)], FEE)["k"]
    joined, _ = _paper_lines(FEE)
    assert joined[span[0]:span[1]].startswith("opłatę skarbową należy wnieść")
    assert joined[span[0]:span[1]].endswith("przed złożeniem wniosku")
    assert span_lines(span, FEE) == (1, 1)


@pytest.mark.parametrize("quote", ["Opłatę skarbową należy wnieść ... przed wysłaniem pisma",  # a part not on paper
                                   "Opłatę skarbową należy wnieść ... wniosku",  # a one-word part proves nothing
                                   "przed złożeniem wniosku ... Opłatę skarbową",  # parts out of order
                                   "Opłatę skarbową należy wnieść ... przed złożeniem wnios"])  # half a word
def test_elided_quote_needs_every_part_exact_and_in_order(quote):
    # no span by parts; place_quotes then falls back to its fuzzy match as for any other quote
    assert _elided_span(quote, _paper_lines(FEE)[0], 0) is None


def test_elided_quote_parts_must_sit_close_together():
    far = "Opłata za wydanie zaświadczenia\n" + "Pouczenie o skutkach prawnych.\n" * 12 + "wynosi 17 zł od egzemplarza\n"
    assert _elided_span("Opłata za wydanie ... wynosi 17 zł", _paper_lines(far)[0], 0) is None
    near = "Opłata za wydanie zaświadczenia\nwynosi 17 zł od egzemplarza\n"
    assert _elided_span("Opłata za wydanie ... wynosi 17 zł", _paper_lines(near)[0], 0) is not None


def test_elided_quote_starts_at_the_first_part_nearest_the_rest():
    text = "Imię i nazwisko ojca\nPESEL ojca\nImię i nazwisko matki\nAdres zamieszkania matki\n"
    span = place_quotes([("k", "Imię i nazwisko ... zamieszkania matki", True)], text)["k"]
    assert span_lines(span, text) == (2, 3)


def test_elided_quote_follows_the_cursor():
    text = "Imię i nazwisko ojca\nAdres zamieszkania ojca\nImię i nazwisko matki\nAdres zamieszkania matki\n"
    joined = _paper_lines(text)[0]
    after_first = joined.index("imię i nazwisko matki")
    assert span_lines(_elided_span("Imię i nazwisko ... Adres zamieszkania", joined, after_first), text) == (2, 3)
    assert span_lines(_elided_span("Imię i nazwisko ... Adres zamieszkania", joined, len(joined)), text) == (2, 3)
    assert span_lines(_elided_span("Imię i nazwisko ... Adres zamieszkania", joined, 0), text) == (0, 1)
