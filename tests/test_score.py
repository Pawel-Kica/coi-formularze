"""Benchmark scoring: identical inputs score 100%, differences are reported."""

import copy
import importlib.util
from pathlib import Path

from app.builder import build
from app.plan import Plan

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("bench_score", ROOT / "bench" / "score.py")
score = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(score)


def gold(name):
    return Plan.model_validate_json((ROOT / "gold" / "plans" / f"{name}.json").read_text())


def test_norm_and_sim():
    assert score.norm("TERYT_DOWODOWY") == score.norm("TERYT_4G_DOWODOWY")
    assert score.sim("Zgoda_sekcja", "Zgoda") >= score.THRESHOLD
    assert score.sim("Plik z potwierdzeniem przelewu", "Potwierdzenie przelewu") >= score.THRESHOLD
    assert score.sim("Numer PESEL", "Kwota") < score.THRESHOLD


def test_score_coi_identity_and_missing():
    coi = build(gold("form5"))
    rep = score.score_coi(coi, coi)
    assert rep["recall"]["overall"] == 1.0
    cut = copy.deepcopy(coi)
    sec = cut["tabs"][1]["questions"][1]["field"]
    sec["repeatingSectionGrid"] = sec["repeatingSectionGrid"][:1]  # drop the two dependent fields
    rep = score.score_coi(cut, coi)
    assert rep["total"]["dependencies"] - rep["found"]["dependencies"] == 2
    assert any("Numer rachunku" in m for m in rep["missing"]["fields"])
    assert "missing field" in rep["summary"]


def test_score_coi_matches_renamed_tab_by_content():
    coi = build(gold("form1"))
    ref = copy.deepcopy(coi)
    ref["tabs"][2]["title"] = "Kontakt z urzędem"
    assert score.score_coi(coi, ref)["recall"]["tabs"] == 1.0


def test_score_plan():
    g = gold("form5")
    assert score.score_plan(g, g)["metrics"]["score"] == 1.0
    o = g.model_copy(deep=True)
    o.pages[0].sections[0].fields[1].type = "TEXT_FIELD"
    o.pages[0].sections[1].fields[2].show_if = None
    o.applicant.pesel = False
    del o.pages[0].sections[2]
    rep = score.score_plan(o, g)
    m = rep["metrics"]
    assert m["fields_precision"] == 1.0 and m["fields_recall"] < 1.0
    assert m["type_agreement"] < 1.0 and m["conditions_recall"] == 0.5
    assert rep["applicant"]["pesel"] is False
    assert "Plik deklaracji podatkowej" in rep["missing_fields"]


def test_cli(capsys, tmp_path):
    p = tmp_path / "g.json"
    p.write_text(gold("form1").model_dump_json())
    assert score.main(["plan", str(p), str(p)]) == 0
    assert "score 100%" in capsys.readouterr().out


def test_score_plan_v2_designed_split_and_precision():
    g = gold("form8")
    m = score.score_plan(g, g, designed=score.load_designed("8"))["metrics"]
    assert m["v2"] == 1.0 and m["conditions_n"] == "15/12+3"
    o = g.model_copy(deep=True)
    for p in o.pages:
        for s in p.sections:
            if s.id == "s4":  # a designed one: out of the headline recall
                s.show_if = None
    m = score.score_plan(o, g, designed=score.load_designed("8"))["metrics"]
    assert m["conditions_recall_paper"] == 1.0 and m["conditions_recall_designed"] < 1.0
    assert m["conditions_precision_v2"] == 1.0
    none = g.model_copy(deep=True)
    for p in none.pages:
        for s in p.sections:
            s.show_if = None
            for f in s.fields:
                f.show_if = None
    m = score.score_plan(none, g, designed=score.load_designed("8"))["metrics"]
    assert m["conditions_precision_v2"] == 0.0 and m["conditions_f1"] == 0.0  # no conditions is not "precise"


def test_score_plan_v2_section_condition_equals_its_only_field():
    g = gold("form8")
    o = g.model_copy(deep=True)
    sec = next(s for p in o.pages for s in p.sections if s.id == "s12")  # one field, condition on the section
    assert len(sec.fields) == 1
    sec.fields[0].show_if, sec.show_if = sec.show_if, None
    m = score.score_plan(o, g)["metrics"]
    assert m["conditions_f1"] == 1.0 and m["conditions_recall"] < 1.0  # old metric missed it


def test_paper_options_match_reworded_option_on_the_same_spot():
    paper = "1. Sposób zwrotu:\n[ ] gotówką w kasie Urzędu Miejskiego   [ ] przelewem na rachunek nr\n"
    po = score.PaperOptions(paper)
    assert score.sim("Gotówką w kasie urzędu miejskiego", "Odbiorę w kasie") < score.THRESHOLD
    assert po.sim("gotówką w kasie Urzędu", "w kasie Urzędu Miejskiego") >= score.THRESHOLD
    assert po.sim("gotówką w kasie Urzędu", "przelewem na rachunek") < score.THRESHOLD
    assert po.sim("Tak", "Nie") < score.THRESHOLD


def test_lints():
    g = gold("form7")
    o = g.model_copy(deep=True)
    fields = {f.id: f for p in o.pages for s in p.sections for f in s.fields}
    c = fields["f7"].show_if
    c.options, c.negate = list(fields[c.field].options), True  # every option, negated
    paper = "Oznakowanie: ☐ mikroprocesor ☐ tatuaż ☐ brak\nKolor: ☐ zielony ☐ fioletowy\nPole X (wypełnić tylko w przypadku psa rasy Y)\n"
    r = score.lints(o, paper)
    assert r["counts"]["vacuous"] == 1
    assert r["box_group_no_field"] == ["Kolor: ☐ zielony ☐ fioletowy"]
    assert r["counts"]["conditional_no_condition"] == 1
    o2 = g.model_copy(deep=True)
    secs = o2.pages[0].sections
    f6 = next(f for s in o2.pages[0].sections for f in s.fields if f.id == "f6")
    assert secs and f6
    s_of = next(s for s in secs if f6 in s.fields)
    s_of.fields.remove(f6)
    s_of.fields.append(f6)  # trigger now after its targets
    assert score.lints(o2, paper)["counts"]["late_trigger"] >= 1
