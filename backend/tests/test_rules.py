"""Rules checker: references pass, broken inputs are caught."""

import copy
import glob
import json
from pathlib import Path

import pytest

from app.coi.builder import build
from app.coi.rules import check, query_atoms
from app.core.plan import Plan

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def coi() -> dict:
    return build(Plan.model_validate_json((ROOT / "tests" / "fixtures" / "plans" / "form5.json").read_text()))


def first(coi, tech):
    for t in coi["tabs"]:
        for q in t["questions"]:
            if q["field"]["techName"] == tech:
                return q["field"]
            for c in q["field"]["repeatingSectionGrid"]:
                if c["field"]["techName"] == tech:
                    return c["field"]
    raise KeyError(tech)


@pytest.mark.parametrize("n", [1, 5])
def test_references_pass(n):
    found = glob.glob(str(ROOT / "data" / "coi" / f"{n}.*" / "konfiguracja*.json"))
    if not found:
        pytest.skip("COI reference JSONs not available")
    assert check(json.loads(Path(found[0]).read_text())) == []


def test_built_passes(coi):
    assert check(coi) == []


def broken(coi, mutate) -> list[str]:
    c = copy.deepcopy(coi)
    mutate(c)
    return check(c)


def has(errors, text):
    return any(text in e for e in errors), errors


@pytest.mark.parametrize("mutate, text", [
    (lambda c: c.update(title="x" * 256), "longer than 255"),
    (lambda c: c.update(tabs=c["tabs"][-1:]), "at least two tabs"),
    (lambda c: c["tabs"].reverse(), "must be the last"),
    (lambda c: c["tabs"][0].update(type="START"), "neither MAIN nor FINISH"),
    (lambda c: c["tabs"][1].update(questions=[]), "page has no fields"),
    (lambda c: c["flow"]["links"].pop(1), "not reachable"),
    (lambda c: c["flow"]["links"][0].update(target="nope"), "not a tab id"),
    (lambda c: first(c, "kwota").update(techName="uzasadnienie"), "not unique"),
    (lambda c: first(c, "kwota").update(id=first(c, "uzasadnienie")["id"]), "not unique"),
    (lambda c: first(c, "kwota").update(dictionaryName="Kraje"), "dictionary allowed only"),
    (lambda c: first(c, "wybierz_co_mamy_zrobic_z_nadplata").update(options=[]), "needs options"),
    (lambda c: first(c, "kwota").update(validation=["REQUIRED", "FOO"]), "unknown validator"),
    (lambda c: first(c, "kwota").update(type="WIDGET"), "unknown field type"),
    (lambda c: first(c, "kwota").update(techName="kwota zł"), "ascii"),
    (lambda c: c["tabs"][1]["questions"].append(copy.deepcopy(c["tabs"][1]["questions"][0]["field"]
                                                              ["repeatingSectionGrid"][0])), "directly on the page"),
    (lambda c: first(c, "informacje_o_nadplacie")["repeatingSectionGrid"].append(
        copy.deepcopy(c["tabs"][1]["questions"][2])), "cannot be nested"),
    (lambda c: first(c, "podglad_1").update(type="TEXT_FIELD"), "FINISH page cannot hold input"),
    (lambda c: c["tabs"][1].update(title="Dane"), "not unique"),
    (lambda c: first(c, "zgoda")["consentSectionConfig"].update(consents=[]), "at least one consent"),
    (lambda c: c["tabs"][0].pop("questions"), "schema"),
    (lambda c: first(c, "kwota").update(validation="REQUIRED"), "schema"),
])  # fmt: skip
def test_catches(coi, mutate, text):
    assert has(broken(coi, mutate), text)[0], broken(coi, mutate)


def _q(c):
    return first(c, "nazwa_podatku")["states"][0]


@pytest.mark.parametrize("edit, text", [
    (lambda q: q.replace("Zaliczyć", "Zaliczyc"), "does not have"),
    (lambda q: q.replace("'Informacje o nadpłacie'", "'Nadpłata'"), "unknown tab"),
    (lambda q: q.replace("rozliczenie_nadplaty", "rozliczenie"), "unknown section"),
    (lambda q: q.replace("wybierz_co_mamy", "co_mamy"), "unknown field"),
    (lambda q: q + " and (", "does not parse"),
    (lambda q: q + " and $exists(x)", "unsupported parts"),
    (lambda q: "true", "no recognised"),
    (lambda q: q.replace("wybierz_co_mamy_zrobic_z_nadplata", "kwota").replace(
        "rozliczenie_nadplaty", "informacje_o_nadplacie"), "not a choice field"),
])  # fmt: skip
def test_catches_bad_jsonata(coi, edit, text):
    c = copy.deepcopy(coi)
    _q(c)["query"] = edit(_q(c)["query"])
    assert has(check(c), text)[0], check(c)


def test_query_atoms_negation():
    atoms, _rest = query_atoms(
        "$not(\"Polska\" in tabs['Dane' in title].questions['a' in field.techName].field"
        ".repeatingSectionGrid['k' in field.techName].field.__userAnswer) or "
        "(\"Tak\" in tabs['T' in title].questions['s' in field.techName].field.__userAnswer"
        "[$repeatingSectionIndex]['f' in fieldTechName].answers)"
    )
    assert [tuple(a) for a in atoms] == [("Polska", "Dane", "a", "k"), ("Tak", "T", "s", "f")]
    assert [a.negated for a in atoms] == [True, False]
    assert atoms[1].repeating and atoms[1].same_row
