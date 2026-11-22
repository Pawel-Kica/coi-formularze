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


def test_invented_options_go_and_a_select_with_an_invented_option_becomes_text():
    radio = f("f5", "Rodzaj robót", "3. Rodzaj robót", FieldType.RADIO, ["Budowa nowej sieci", "Roboty inne niż wymienione", "Remont mostu"])
    # mostly paper words, one made up: still a list the paper does not have (cutting it loses real answers)
    select = f("f6", "Źródło dochodu", "źródło dochodu", FieldType.SELECT, ["Budowa sieci", "Roboty inne", "Pension"])
    real = f("f7", "Rodzaj sieci", "3. Rodzaj robót", FieldType.SELECT, ["Budowa nowej sieci", "Roboty inne niż wymienione"])
    out, dropped = filter_plan(plan(sec("s1", [radio, select, real])), PAPER)
    got = fields_of(out)
    assert got["f5"].options == ["Budowa nowej sieci", "Roboty inne niż wymienione"]
    assert got["f6"].type == FieldType.TEXT_FIELD and got["f6"].options == []
    assert got["f7"].type == FieldType.SELECT and len(got["f7"].options) == 2
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
    # split fields that share a label and a quote are not duplicates when their sections tell them apart
    w = [f(f"f{n}", "Szerokość", "4. Nazwa inwestycji", FieldType.NUMBER) for n in (1, 2)]
    out, dropped = filter_plan(plan(sec("s1", [w[0]], title="Jezdnia"), sec("s2", [w[1]], title="Chodnik")), PAPER)
    assert len(fields_of(out)) == 2 and not dropped


def test_a_field_read_twice_at_a_page_cut_is_kept_once():
    """A JPG of stacked pages: both pages hold the line at the cut, so the model returns its field twice."""
    name = lambda id, req=True: f(id, "Nazwa inwestycji", "4. Nazwa inwestycji", required=req)
    cond = Condition(field="f8", options=["Tak"], quote="Czy roboty wymagają objazdu")
    detour = [f(id, "Czy roboty wymagają objazdu?", "5. Czy roboty wymagają objazdu: tak / nie", FieldType.RADIO, ["Tak", "Nie"])
              for id in ("f7", "f8")]
    length = f("f10", "Długość objazdu", "Długość objazdu", show_if=cond)
    p = plan(sec("s1", [name("f1", False), name("f2"), detour[0]], title="Inwestycja"),
             sec("s2", [name("f4"), detour[1], length], title="Inwestycja"),  # the cut also repeated the heading
             sec("s3", [name("f3")], title="Inna inwestycja"))  # another section: a field of its own
    out, dropped = filter_plan(p, PAPER)
    got = fields_of(out)
    assert sorted(got) == ["f1", "f10", "f3", "f8"]  # the choice question: the one the condition uses stays
    assert got["f1"].required and got["f10"].show_if.field == "f8"
    assert [s.id for s in out.pages[0].sections] == ["s1", "s2", "s3"]
    # the trigger and the field it gates both read twice: the gated copy goes too
    ask = lambda id: f(id, "Czy roboty wymagają objazdu?", "5. Czy roboty wymagają objazdu: tak / nie", FieldType.RADIO, ["Tak", "Nie"])
    gated = lambda id, on: f(id, "Długość objazdu", "Długość objazdu", show_if=Condition(field=on, options=["Tak"], quote="Długość objazdu"))
    p = plan(sec("s1", [name("f1"), ask("f7"), gated("f10", "f7")], title="Inwestycja"),
             sec("s2", [ask("f8"), gated("f11", "f8"), f("f12", "Rodzaj robót", "3. Rodzaj robót")], title="Inwestycja"))
    out, dropped = filter_plan(p, PAPER + "5. Czy roboty wymagają objazdu: tak / nie\nDługość objazdu ____\n")
    assert sorted(fields_of(out)) == ["f1", "f10", "f12", "f7"]
    # a block the paper prints twice is not a line read twice
    owner = lambda n: sec(f"o{n}", [f(f"o{n}a", "Nazwa inwestycji", "4. Nazwa inwestycji"),
                                    f(f"o{n}b", "Długość objazdu", "Długość objazdu")], title=f"{n}. Dane współwłaściciela")
    out, dropped = filter_plan(plan(owner(1), owner(2)), PAPER)
    assert len(fields_of(out)) == 4 and not dropped


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
    # no earlier section to take the question: it gets its own, the address section is renamed
    out, _ = filter_plan(plan(address(), sec("s3", [f("f1", "Nazwa inwestycji", "Nazwa inwestycji")])), paper)
    assert [s.title for s in out.pages[0].sections] == ["Adres do korespondencji", "Inny adres do korespondencji", "S"]
    assert out.pages[0].sections[1].show_if.field == out.pages[0].sections[0].fields[0].id


def test_correspondence_address_the_model_already_gates_gets_no_second_question():
    paper = ADDRESS.replace("Adres do korespondencji", "Adres do korespondencji\nWypełnij, jeżeli adres do korespondencji jest inny niż adres zamieszkania.")
    own = f("f9", "Czy adres do korespondencji jest inny niż adres zamieszkania?", "jeżeli adres do korespondencji jest inny",
            FieldType.RADIO, ["Tak", "Nie"])
    gated = f("f3", "Ulica", "Ulica", show_if=Condition(field="f9", options=["Tak"], quote="jeżeli adres do korespondencji jest inny"))
    # the model's question sits inside the address section and gates its fields: the Standards block would swallow
    # both, so the question moves to the section before and gates the whole address
    p = plan(sec("s1", [f("f1", "Nazwa inwestycji", "Nazwa inwestycji")]),
             sec("s2", [own, gated, f("f4", "Miejscowość", "Miejscowość")], title="Adres do korespondencji", address=True))
    out, notes = filter_plan(p, paper)
    labels = [x.label for x in fields_of(out).values()]
    s1, s2 = out.pages[0].sections
    assert CORRESPONDENCE_QUESTION not in labels and [x.id for x in s1.fields] == ["f1", "f9"]
    assert s2.show_if.field == "f9" and s2.show_if.options == ["Tak"] and any(n.startswith("address:") for n in notes)
    # the address section's own condition on it: the question gets a section of its own, ungated
    gate = Condition(field="f9", options=["Tak"], quote="jeżeli adres do korespondencji jest inny")
    p = plan(sec("s2", [own, f("f4", "Miejscowość", "Miejscowość")], title="Adres do korespondencji", address=True, show_if=gate))
    out, _ = filter_plan(p, paper)
    s1, s2 = out.pages[0].sections
    assert [x.id for x in s1.fields] == ["f9"] and s1.show_if is None and s2.show_if.field == "f9"
    # its own wording earlier, not a plain Tak/Nie: left to the model too
    kind = f("f9", "Adres do korespondencji", "jeżeli adres do korespondencji jest inny", FieldType.RADIO,
             ["Taki sam jak zamieszkania", "Inny"])
    out, _ = filter_plan(plan(sec("s1", [kind]), address()), paper)
    assert CORRESPONDENCE_QUESTION not in [x.label for x in fields_of(out).values()]


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


PARTS = """Rodzaje wniosków:
I. Wniosek o przyznanie stypendium sportowego
II. Wniosek o przyznanie nagrody sportowej
## I. Wniosek o przyznanie stypendium sportowego
Imię ____
Dyscypliny sportowe: [ ] piłka nożna [ ] karate
## II. Wniosek o przyznanie nagrody sportowej
Imię ____
Opis zdobytej nagrody ____
"""


def test_a_condition_quoting_the_heading_of_the_part_the_target_sits_in_is_kept():
    # the trigger quotes the list item, the condition the same words printed again as the part heading
    kind = f("f1", "O co wnioskujesz?", "I. Wniosek o przyznanie stypendium sportowego", FieldType.RADIO,
             ["Stypendium sportowe", "Nagroda sportowa"])
    heading = "I. Wniosek o przyznanie stypendium sportowego"
    disc = f("f2", "Dyscypliny", "Dyscypliny sportowe:", FieldType.CHECKBOX, ["Piłka nożna", "Karate"])
    prize = f("f3", "Opis nagrody", "Opis zdobytej nagrody")
    p = plan(sec("s1", [kind]), sec("s4", [f("f4", "Imię", "Imię")]),
             sec("s2", [disc], show_if=Condition(field="f1", options=["Stypendium sportowe"], quote=heading)),
             sec("s3", [prize], show_if=Condition(field="f1", options=["Stypendium sportowe"], quote=heading)))
    out, notes = filter_plan(p, PARTS)
    s1, s4, s2, s3 = out.pages[0].sections
    assert s2.show_if is not None
    # the part I heading does not reach a target printed under part II
    assert s3.show_if is None and any("condition on s3" in n for n in notes)


def test_a_statement_printed_twice_is_kept_once_but_a_different_one_stays():
    from app.plan import Statement

    p = plan(sec("s1", [f("f4", "Nazwa inwestycji", "4. Nazwa inwestycji")]))
    p.statements = [Statement(text="Znam regulamin przyznawania stypendiów.", quote="Nazwa inwestycji", required=False),
                    Statement(text="Znam regulamin przyznawania nagród.", quote="Nazwa inwestycji"),
                    Statement(text="Znam regulamin przyznawania stypendiów", quote="Nazwa inwestycji")]
    out, notes = filter_plan(p, PAPER)
    assert [st.text for st in out.statements] == ["Znam regulamin przyznawania stypendiów.", "Znam regulamin przyznawania nagród."]
    assert out.statements[0].required and any(n.startswith("duplicate: statement") for n in notes)


def test_a_condition_over_every_option_of_a_required_single_choice_is_cleared():
    # "Opinia klubu" printed in every part of a paper joining request kinds: shown for each kind = always shown
    every = Condition(field="f5", options=["Budowa nowej sieci", "Roboty inne niż wymienione"], quote="3. Rodzaj robót")
    some = Condition(field="f5", options=["Budowa nowej sieci"], quote="jeżeli wnioskodawca nie jest właścicielem terenu")
    p = plan(sec("s1", [WORKS]), sec("s2", [f("f4", "Nazwa inwestycji", "4. Nazwa inwestycji")], show_if=every),
             sec("s3", [f("f6", "Źródło dochodu", "źródło dochodu")], show_if=some))
    out, notes = filter_plan(p, PAPER)
    s1, s2, s3 = out.pages[0].sections
    assert s2.show_if is None and fields_of(out)["f4"].required and any("always shown" in n for n in notes)
    assert s3.show_if is not None
    # an optional trigger may stay unanswered: the condition still hides the section then, so it stays
    loose = WORKS.model_copy(update={"required": False})
    out, _ = filter_plan(plan(sec("s1", [loose]), sec("s2", [f("f4", "Nazwa inwestycji", "4. Nazwa inwestycji")], show_if=every)), PAPER)
    assert out.pages[0].sections[1].show_if is not None


def test_a_field_shown_by_its_own_condition_is_required_unless_the_paper_says_optional():
    # "[ ] tablica ____": the number is asked only for the chosen option, so it must be filled then
    cond = Condition(field="f2", options=["Tablica"], quote="2. Sposób oznakowania:")
    opt = Condition(field="f2", options=["Taśma"], quote="2. Sposób oznakowania:")
    p = plan(sec("s1", [MARKING, f("f3", "Numer tablicy", "tablica", show_if=cond, required=False),
                        f("f4", "Numer taśmy (opcjonalnie)", "taśma", show_if=opt, required=False),
                        f("f7", "Co oznakujesz?", "taśma", FieldType.CHECKBOX, ["Wjazd", "Wyjazd"], show_if=opt, required=False)]))
    out, notes = filter_plan(p, PAPER)
    got = fields_of(out)
    assert got["f3"].required is True and any(n.startswith("required: field f3") for n in notes)
    assert got["f4"].required is False and got["f7"].required is False
    # a field without a condition keeps the model's choice
    out, _ = filter_plan(plan(sec("s1", [f("f4", "Nazwa inwestycji", "4. Nazwa inwestycji", required=False)])), PAPER)
    assert fields_of(out)["f4"].required is False


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
