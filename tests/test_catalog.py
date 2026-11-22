"""Dictionary catalog (app/catalog.py): API round trip on a temp DB, COI dictionaries read-only, merge without duplicates."""

import pytest
from fastapi.testclient import TestClient

from app.api import app
from app.config import settings

BREEDS = "RASY_PSOW_UZNAWANYCH_ZA_AGRESYWNE"


@pytest.fixture(autouse=True)
def temp_db(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "history_db", tmp_path / "history.sqlite3")
    monkeypatch.setattr(settings, "auth_enabled", False)


@pytest.fixture
def client():
    return TestClient(app)


def test_empty_catalog_lists_coi_dictionaries_read_only(client):
    codes = [(d["code"], d["builtin"]) for d in client.get("/api/dictionaries").json()]
    assert codes == [("Kraje", True), ("TERYT_4G_DOWODOWY", True), ("Numery kierunkowe", True)]
    assert client.put("/api/dictionaries/Kraje", json={"items": ["X"]}).status_code == 403
    assert client.delete("/api/dictionaries/Kraje").status_code == 403
    assert client.post("/api/dictionaries/Kraje/items", json={"items": ["X"]}).status_code == 403


def test_demo_path_new_empty_dictionary_then_values(client):
    r = client.post("/api/dictionaries", json={"name": "Rasy psów uznawanych za agresywne", "code": BREEDS,
                                               "source": {"file": "wniosek-psy.pdf", "field": "Rasa psa"}, "items": []})
    assert r.status_code == 201
    d = r.json()
    assert (d["code"], d["items"], d["sources"]) == (BREEDS, [], [{"file": "wniosek-psy.pdf", "field": "Rasa psa"}])

    for name in ["Amerykański pit bull terier", "Akbash dog", "Tosa inu"]:
        items = client.get(f"/api/dictionaries/{BREEDS}").json()["items"]
        client.put(f"/api/dictionaries/{BREEDS}", json={"items": items + [{"nazwa": name}]})
    d = client.get(f"/api/dictionaries/{BREEDS}").json()
    assert d["items"] == [{"kod": "AMERYKANSKI_PIT_BULL_TERIER", "nazwa": "Amerykański pit bull terier"},
                          {"kod": "AKBASH_DOG", "nazwa": "Akbash dog"}, {"kod": "TOSA_INU", "nazwa": "Tosa inu"}]

    # "×" on the page = PUT without that value; codes of the others stay
    client.put(f"/api/dictionaries/{BREEDS}", json={"items": d["items"][1:]})
    assert [i["kod"] for i in client.get(f"/api/dictionaries/{BREEDS}").json()["items"]] == ["AKBASH_DOG", "TOSA_INU"]


def test_add_to_existing_merges_without_duplicates(client):
    client.post("/api/dictionaries", json={"name": "Dyscypliny", "items": [{"kod": "HOKEJ", "nazwa": "hokej"}, "karate"]})
    d = client.post("/api/dictionaries/DYSCYPLINY/items",
                    json={"items": ["Hokej", " karate ", "piłka nożna"], "source": {"file": "b.pdf", "field": "Dyscyplina"}}).json()
    assert [i["nazwa"] for i in d["items"]] == ["hokej", "karate", "piłka nożna"]
    assert d["sources"] == [{"file": "b.pdf", "field": "Dyscyplina"}]


def test_taken_code_gets_a_suffix_and_delete(client):
    a = client.post("/api/dictionaries", json={"name": "Rodzaje", "code": "RODZAJE"}).json()
    b = client.post("/api/dictionaries", json={"name": "Rodzaje", "code": "RODZAJE"}).json()
    kraje = client.post("/api/dictionaries", json={"name": "Moje kraje", "code": "Kraje"}).json()
    assert (a["code"], b["code"]) == ("RODZAJE", "RODZAJE_2") and kraje["code"] != "Kraje"
    assert client.delete("/api/dictionaries/RODZAJE").status_code == 204
    assert client.get("/api/dictionaries/RODZAJE").status_code == 404
    assert client.delete("/api/dictionaries/RODZAJE").status_code == 404
    assert client.post("/api/dictionaries", json={"name": "  "}).status_code == 400


def test_auth_required_when_enabled(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    assert client.get("/api/dictionaries").status_code == 401


def test_items_must_be_a_list(client):
    assert client.post("/api/dictionaries", json={"name": "X", "items": "abc"}).status_code == 400


def test_field_bound_by_dodaj_passes_the_coi_rules(client):
    """What "Dodaj" makes of a text field (editor.js edBindDictionary): a SELECT with only dictionaryName, no options."""
    from pathlib import Path

    from app.rules import check

    from app.builder import build
    from app.plan import Plan

    coi = build(Plan.model_validate_json(Path("gold/plans/form1.json").read_text()))
    field = next(c["field"] for t in coi["tabs"] for q in t["questions"] for c in q["field"].get("repeatingSectionGrid") or []
                 if c["field"]["type"] == "TEXT_FIELD" and not c["field"].get("states"))
    assert check(coi) == []
    for k in ("readOnly", "defaultValueType"):
        field.pop(k, None)
    field.update(type="SELECT", options=[], validation=["REQUIRED"], dictionaryName=BREEDS, multiLevelTree=False,
                 searchEnabled=True, defaultValue=[])
    assert check(coi) == []
