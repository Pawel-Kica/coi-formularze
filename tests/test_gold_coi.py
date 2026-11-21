"""gold/coi/formN.json: the COI reference JSONs with hand-added validation (see gold/coi/NOTES.md).

They must equal the COI originals except `validation`, and the builder fed with the gold
plans must reach the `walid=` bar (validator F1 >= 0.95) against them."""

import copy
import glob
import importlib.util
import json
from pathlib import Path

import pytest

from app.builder import build
from app.plan import Plan
from app.rules import VALIDATORS, check

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("bench_score", ROOT / "bench" / "score.py")
score = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(score)
FORMS = ("1", "5")


def gold_coi(form: str) -> dict:
    return json.loads((ROOT / "gold" / "coi" / f"form{form}.json").read_text())


def original(form: str) -> dict:
    found = glob.glob(str(ROOT / "data" / "coi" / f"{form}.*" / "konfiguracja*.json"))
    if not found:
        pytest.skip("COI reference JSONs not available (data/ symlink)")
    return json.loads(Path(found[0]).read_text())


def without_validation(x):
    if isinstance(x, dict):
        return {k: without_validation(v) for k, v in x.items() if k != "validation"}
    if isinstance(x, list):
        return [without_validation(v) for v in x]
    return x


@pytest.mark.parametrize("form", FORMS)
def test_gold_equals_original_except_validation(form):
    assert without_validation(gold_coi(form)) == without_validation(original(form))


@pytest.mark.parametrize("form", FORMS)
def test_gold_validation_is_valid(form):
    g = gold_coi(form)
    assert check(g) == []
    fields = [c["field"] for t in g["tabs"] for q in t["questions"] for c in q["field"].get("repeatingSectionGrid") or []]
    assert all(isinstance(f["validation"], list) and set(f["validation"]) <= VALIDATORS for f in fields)


@pytest.mark.parametrize("form", FORMS)
def test_builder_from_gold_plan_hits_walid(form):
    coi = build(Plan.model_validate_json((ROOT / "gold" / "plans" / f"form{form}.json").read_text()))
    rep = score.score_coi(coi, gold_coi(form))
    v = rep["validation"]
    assert not v["missing"] and not v["extra"], v


def test_score_validation_counts_pairs():
    g = gold_coi("5")
    assert score.score_coi(g, g)["validation"]["f1"] == 1.0
    ours = copy.deepcopy(g)
    pesel = next(c["field"] for q in ours["tabs"][0]["questions"] for c in q["field"].get("repeatingSectionGrid") or []
                 if c["field"]["techName"] == "numer_pesel")
    pesel["validation"] = ["REQUIRED", "EMAIL"]  # PESEL missing, EMAIL extra
    v = score.score_coi(ours, g)["validation"]
    assert len(v["missing"]) == 1 and "PESEL" in v["missing"][0]
    assert len(v["extra"]) == 1 and "EMAIL" in v["extra"][0]
    assert v["f1"] < 1.0
