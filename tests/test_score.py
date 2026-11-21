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
