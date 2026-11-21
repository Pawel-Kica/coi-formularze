"""Post-model checks: condition support (app.quotes) and plan repairs (app.repair).
Each test is one defect a judge found on a real form, rebuilt on a made-up paper."""

from app.plan import Condition, FieldType, Page, Plan, PlanField, Section
from app.quotes import filter_plan, quote_lines
from app.repair import CORRESPONDENCE_QUESTION, option_on_paper

PAPER = """Wniosek o zezwolenie na zajęcie placu
1. Rodzaj zajęcia: (do 20% szerokości / powyżej 20% do 50% / powyżej 50%)
2. Sposób oznakowania:
[ ] tablica ____
[ ] taśma ____
[ ] brak
3. Rodzaj robót
[ ] budowa nowej sieci
[ ] roboty inne niż wymienione
4. Nazwa inwestycji ____
5. Czy roboty wymagają objazdu: tak / nie
Długość objazdu ____
6. Dochód: źródło dochodu ____
Załączniki:
1) Plan sytuacyjny w skali 1:500
2) Projekt organizacji ruchu
3) Zgoda właściciela, jeżeli wnioskodawca nie jest właścicielem terenu
"""


def f(id, label, quote, type=FieldType.TEXT_FIELD, options=(), show_if=None, required=True):
    return PlanField(id=id, label=label, type=type, options=list(options), quote=quote, show_if=show_if, required=required)


def plan(*sections):
    return Plan(title="Wniosek", addressee="", pages=[Page(title="P", sections=list(sections))])


def sec(id, fields, title="S", **kw):
    return Section(id=id, title=title, fields=fields, **kw)


def fields_of(p):
    return {x.id: x for pg in p.pages for s in pg.sections for x in s.fields}


WIDTH = f("f1", "Jaką część szerokości zajmiesz?", "(do 20% szerokości / powyżej 20% do 50% / powyżej 50%)", FieldType.RADIO,
          ["Do 20% szerokości", "Powyżej 20% do 50%", "Powyżej 50%"])
MARKING = f("f2", "Jak oznakujesz teren?", "2. Sposób oznakowania:", FieldType.RADIO, ["Tablica", "Taśma", "Brak"])
WORKS = f("f5", "Rodzaj robót", "3. Rodzaj robót", FieldType.RADIO, ["Budowa nowej sieci", "Roboty inne niż wymienione"])


def test_quote_lines_counts_non_empty_lines():
    assert quote_lines("4. Nazwa inwestycji", PAPER) == [(9, 9)]  # 0-based
    assert quote_lines("tego nie ma na papierze wcale", PAPER) == []


def test_condition_quoting_only_the_trigger_far_from_the_target_is_dropped_and_target_not_required():
    # attachments hung on an unrelated question, "proved" with that question's own options line
    cond = Condition(field="f1", options=["Powyżej 50%"], quote="do 20% szerokości / powyżej 20% do 50% / powyżej 50%")
    p = plan(sec("s1", [WIDTH, f("f9", "Plik z planem sytuacyjnym", "1) Plan sytuacyjny w skali 1:500", FieldType.FILE_UPLOADER, show_if=cond)]))
    out, dropped = filter_plan(p, PAPER)
    target = fields_of(out)["f9"]
    assert target.show_if is None and target.required is False
    assert any("only repeats the trigger" in d for d in dropped)


def test_condition_quoting_the_trigger_is_kept_when_the_target_is_on_the_chosen_option_line():
    # "[ ] tablica ____": the blank belongs to the option
    cond = Condition(field="f2", options=["Taśma"], quote="2. Sposób oznakowania:")
    p = plan(sec("s1", [MARKING, f("f3", "Numer taśmy", "taśma", show_if=cond)]))
    out, _ = filter_plan(p, PAPER)
    assert fields_of(out)["f3"].show_if is not None


def test_condition_quoting_the_chosen_option_is_dropped_when_the_target_is_the_next_item():
    # the last option of a list, then the next numbered item of the form
    cond = Condition(field="f5", options=["Roboty inne niż wymienione"], quote="roboty inne niż wymienione")
    p = plan(sec("s1", [WORKS, f("f6", "Nazwa inwestycji", "4. Nazwa inwestycji", show_if=cond)]))
    out, _ = filter_plan(p, PAPER)
    assert fields_of(out)["f6"].show_if is None


def test_yes_no_trigger_reaches_two_lines_below_and_conditional_words_always_count():
    detour = f("f7", "Czy roboty wymagają objazdu?", "5. Czy roboty wymagają objazdu: tak / nie", FieldType.RADIO, ["Tak", "Nie"])
    near = Condition(field="f7", options=["Tak"], quote="Czy roboty wymagają objazdu")
    owner = f("f8", "Czy jesteś właścicielem terenu?", "jeżeli wnioskodawca nie jest właścicielem terenu", FieldType.RADIO, ["Tak", "Nie"])
    marker = Condition(field="f8", options=["Nie"], quote="jeżeli wnioskodawca nie jest właścicielem terenu")
    far = Condition(field="f7", options=["Tak"], quote="Czy roboty wymagają objazdu")
    p = plan(sec("s1", [detour, owner,
                        f("f10", "Długość objazdu", "Długość objazdu", show_if=near),
                        f("f11", "Plik ze zgodą właściciela", "3) Zgoda właściciela", FieldType.FILE_UPLOADER, show_if=marker),
                        f("f12", "Plik z projektem", "2) Projekt organizacji ruchu", FieldType.FILE_UPLOADER, show_if=far)]))
    out, _ = filter_plan(p, PAPER)
    got = fields_of(out)
    assert got["f10"].show_if is not None and got["f11"].show_if is not None and got["f12"].show_if is None


def test_section_right_after_the_trigger_section_keeps_a_condition_that_quotes_the_trigger():
    cond = Condition(field="f7", options=["Tak"], quote="Czy roboty wymagają objazdu")
    detour = f("f7", "Czy roboty wymagają objazdu?", "5. Czy roboty wymagają objazdu: tak / nie", FieldType.RADIO, ["Tak", "Nie"])
    p = plan(sec("s1", [detour]), sec("s2", [f("f9", "Plik z planem", "1) Plan sytuacyjny w skali 1:500")], show_if=cond),
             sec("s3", [f("f6", "Nazwa inwestycji", "4. Nazwa inwestycji")], show_if=cond.model_copy()))
    out, _ = filter_plan(p, PAPER)
    shown = {s.id: s.show_if is not None for s in out.pages[0].sections}
    assert shown == {"s1": False, "s2": True, "s3": False}


def test_choice_question_about_the_applicant_is_not_a_standard_field():
    who = f("f1", "Czy osoba odpowiedzialna to wnioskodawca?", "4. Nazwa inwestycji", FieldType.RADIO, ["Tak", "Nie"])
    name = f("f2", "Nazwisko wnioskodawcy", "4. Nazwa inwestycji")
    out, dropped = filter_plan(plan(sec("s1", [who, name])), PAPER)
    assert list(fields_of(out)) == ["f1"]
    assert len(dropped) == 1 and dropped[0].startswith("standard:")


def test_option_on_paper_is_lenient_about_inflection_and_strict_about_made_up_words():
    assert option_on_paper("Budowa nowych sieci", PAPER)  # inflected
    assert option_on_paper("Tak", PAPER) and option_on_paper("M", PAPER)  # by design / too short to tell
    assert not option_on_paper("Pension", PAPER)
    assert not option_on_paper("Umowa o dzieło", PAPER)


def test_invented_options_go_and_a_select_of_mostly_invented_options_becomes_text():
    radio = f("f5", "Rodzaj robót", "3. Rodzaj robót", FieldType.RADIO, ["Budowa nowej sieci", "Roboty inne niż wymienione", "Remont mostu"])
    select = f("f6", "Źródło dochodu", "źródło dochodu", FieldType.SELECT, ["Emerytura", "Renta", "Pension", "Roboty"])
    out, dropped = filter_plan(plan(sec("s1", [radio, select])), PAPER)
    got = fields_of(out)
    assert got["f5"].options == ["Budowa nowej sieci", "Roboty inne niż wymienione"]
    assert got["f6"].type == FieldType.TEXT_FIELD and got["f6"].options == []
    assert sum(d.startswith("options:") for d in dropped) == 2


def test_the_same_choice_question_twice_is_kept_once_and_conditions_follow():
    a = f("f7", "Czy roboty wymagają objazdu?", "5. Czy roboty wymagają objazdu: tak / nie", FieldType.RADIO, ["Tak", "Nie"])
    b = f("f8", "Czy roboty wymagają objazdu drogowego?", "Czy roboty wymagają objazdu", FieldType.RADIO, ["Tak", "Nie"])
    cond = Condition(field="f8", options=["Tak"], quote="Czy roboty wymagają objazdu")
    p = plan(sec("s1", [a]), sec("s2", [b]), sec("s3", [f("f10", "Długość objazdu", "Długość objazdu", show_if=cond)]))
    out, dropped = filter_plan(p, PAPER)
    got = fields_of(out)
    assert "f7" not in got and got["f10"].show_if.field == "f8"  # the one a condition uses stays
    assert [s.id for s in out.pages[0].sections] == ["s2", "s3"]
    # split fields that share a label and a quote are not duplicates
    w = [f(f"f{n}", "Szerokość", "4. Nazwa inwestycji", FieldType.NUMBER) for n in (1, 2)]
    out, dropped = filter_plan(plan(sec("s1", [w[0]]), sec("s2", [w[1]])), PAPER)
    assert len(fields_of(out)) == 2 and not dropped


ADDRESS = "Adres do korespondencji\nUlica ____ Miejscowość ____\nNazwa inwestycji ____\n"


def address(title="Adres do korespondencji", **kw):
    return sec("s2", [f("f3", "Ulica", "Ulica"), f("f4", "Miejscowość", "Miejscowość")], title=title, address=True, **kw)


def test_correspondence_address_gets_a_question_when_the_paper_says_only_if_other():
    paper = ADDRESS.replace("Adres do korespondencji", "2.2 Dane inwestora (do korespondencji)\nWypełnia się, jeżeli adres do korespondencji jest inny niż w pkt 2.1.")
    p = plan(sec("s1", [f("f1", "Nazwa inwestycji", "Nazwa inwestycji")]), address("Dane inwestora (do korespondencji)"))
    out, dropped = filter_plan(p, paper)
    s1, s2 = out.pages[0].sections
    ask = s1.fields[-1]
    assert ask.label == CORRESPONDENCE_QUESTION and ask.type == FieldType.RADIO and ask.options == ["Tak", "Nie"]
    assert s2.show_if.field == ask.id and s2.show_if.options == ["Tak"]
    assert any(d.startswith("correspondence:") for d in dropped)


def test_correspondence_address_without_a_rule_on_paper_is_dropped_but_not_a_third_persons():
    p = plan(sec("s1", [f("f1", "Nazwa inwestycji", "Nazwa inwestycji")]), address())
    out, dropped = filter_plan(p, ADDRESS)
    assert [s.id for s in out.pages[0].sections] == ["s1"]
    assert any(d.startswith("standard: section s2") for d in dropped)
    p = plan(sec("s1", [f("f1", "Nazwa inwestycji", "Nazwa inwestycji")]), address("Adres do korespondencji kandydata"))
    out, dropped = filter_plan(p, ADDRESS)
    assert len(out.pages[0].sections) == 2 and not dropped
    # one that already has a condition is left alone
    kind = f("f1", "Jak wysłać odpowiedź?", "Nazwa inwestycji", FieldType.RADIO, ["Tak", "Nie"])
    cond = Condition(field="f1", options=["Tak"], quote="jeżeli Adres do korespondencji")
    out, dropped = filter_plan(plan(sec("s1", [kind]), address(show_if=cond)), ADDRESS)
    assert len(out.pages[0].sections) == 2


def test_gold_plans_pass_untouched():
    """Hard check: every condition, option and field of the 9 gold plans survives the filter and the repairs."""
    import pytest

    from bench.refilter import n_conditions
    from bench.run_all import COI, GOLD, coi_files
    from app.extract import extract
    from app.quotes import threshold_for

    if not COI.exists():
        pytest.skip("data/coi not linked")
    total = 0
    for form, path in coi_files(None, {"pdf"}):
        gold = Plan.model_validate_json((GOLD / f"form{form}.json").read_text())
        doc = extract(path.read_bytes(), path.name)
        out, notes = filter_plan(gold, doc.text, threshold_for(doc.method))
        assert not [n for n in notes if n.startswith(("condition", "options", "duplicate", "correspondence"))], (form, notes)
        assert n_conditions(out) == n_conditions(gold)
        total += n_conditions(out)
    assert total == 37
