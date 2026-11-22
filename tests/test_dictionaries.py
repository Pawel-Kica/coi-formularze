"""Dictionary proposals filter (bonus II): code decides what the model proposed, no model here."""

from pathlib import Path

from app.dictionaries import TO_FILL, filter_candidates

TEXT = """Rasa psa: ____
Płeć: [ ] pies [ ] suka
Czy pies jest zaszczepiony? Tak Nie
Sposób oznakowania psa: [ ] mikroprocesor [ ] tatuaż [ ] brak
Miejscowość ____ Województwo ____ Kraj ____
Dyscypliny sportowe: hokej karate piłka nożna Inna dyscyplina ____
Imię i nazwisko ____
Uzasadnienie wniosku ____
Rodzaj nieruchomości: [ ] A [ ] B
Inne
"""


def cand(name, field, quote, values=(), reason="Wspólny dla wielu urzędów."):
    return {"name": name, "field": field, "quote": quote, "values": list(values), "reason": reason}


def run(*candidates):
    kept, dropped = filter_candidates(list(candidates), TEXT)
    return [k["name"] for k in kept], dropped


def test_list_with_values_on_paper_is_kept_with_codes():
    kept, _ = filter_candidates([cand("Dyscypliny sportowe", "Dyscypliny", "Dyscypliny sportowe:",
                                      ["hokej", "karate", "piłka nożna", "Inna"])], TEXT)
    p = kept[0]
    assert p["status"] == "z_formularza"
    assert [i["nazwa"] for i in p["items"]] == ["hokej", "karate", "piłka nożna", "Inna"]
    assert [i["kod"] for i in p["items"]] == ["HOKEJ", "KARATE", "PILKA_NOZNA", "INNA"]
    assert [c["name"] for c in p["structure"]] == ["kod", "nazwa"]
    assert p["field"] == {"label": "Dyscypliny", "quote": "Dyscypliny sportowe:", "types": ["SELECT", "RADIO", "CHECKBOX"]}


def test_blank_closed_set_field_is_kept_to_fill():
    kept, _ = filter_candidates([cand("Rasy psów", "Rasa psa", "Rasa psa: ____")], TEXT)
    assert kept[0]["status"] == "do_uzupelnienia" and kept[0]["items"] == [] and kept[0]["note"] == TO_FILL


def test_value_not_on_paper_drops_the_whole_proposal():
    # the model "knew" more breeds than the paper has: never invent values
    names, dropped = run(cand("Rasy psów", "Rasa psa", "Rasa psa:", ["amstaff", "pitbull"]))
    assert names == [] and "wartości nie ma na formularzu" in dropped[0]


def test_fewer_than_two_real_values_dropped():
    names, dropped = run(cand("Płcie", "Płeć", "Płeć:", ["pies"]),
                         cand("Dyscypliny", "Dyscypliny", "Dyscypliny sportowe:", ["hokej", "Inna"]))
    assert names == [] and all("mniej niż 2" in d for d in dropped)


def test_yes_no_dropped():
    names, dropped = run(cand("Odpowiedzi", "Czy zaszczepiony", "Czy pies jest zaszczepiony?", ["Tak", "Nie"]))
    assert names == [] and "Tak/Nie" in dropped[0]


def test_existing_coi_dictionaries_dropped():
    names, dropped = run(cand("Miejscowości", "Miejscowość", "Miejscowość"),
                         cand("Województwa", "Województwo", "Województwo"),
                         cand("Kraje", "Kraj", "Kraj"))
    assert names == [] and len(dropped) == 3 and all("istniejący słownik" in d for d in dropped)


def test_field_quote_not_on_paper_dropped():
    names, dropped = run(cand("Kolory sierści", "Kolor sierści", "Kolor sierści psa: ____"))
    assert names == [] and "pola nie ma" in dropped[0]


def test_blank_free_text_field_dropped():
    names, dropped = run(cand("Imiona", "Imię i nazwisko", "Imię i nazwisko"),
                         cand("Uzasadnienia", "Uzasadnienie wniosku", "Uzasadnienie wniosku"))
    assert names == [] and all("dowolną treścią" in d for d in dropped)


def test_short_values_need_an_exact_word():
    names, _ = run(cand("Rodzaje nieruchomości", "Rodzaj nieruchomości", "Rodzaj nieruchomości:", ["A", "B"]))
    assert names == ["Rodzaje nieruchomości"]
    names, _ = run(cand("Rodzaje nieruchomości", "Rodzaj nieruchomości", "Rodzaj nieruchomości:", ["A", "Z"]))
    assert names == []


def test_duplicates_dropped_and_values_deduplicated():
    a = cand("Sposoby oznakowania", "Oznakowanie", "Sposób oznakowania psa:", ["mikroprocesor", "tatuaż", "Tatuaż"])
    kept, dropped = filter_candidates([a, dict(a, name="Oznakowania")], TEXT)
    assert len(kept) == 1 and [i["nazwa"] for i in kept[0]["items"]] == ["mikroprocesor", "tatuaż"]
    assert "duplikat" in dropped[0]


def test_garbage_candidates_do_not_crash():
    kept, dropped = filter_candidates([{"name": None, "values": "x"}, {}], TEXT)
    assert kept == [] and len(dropped) == 2


# --- endpoint: same upload and JWT as /api/forms/convert, model monkeypatched ---

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pydantic import SecretStr  # noqa: E402

from app import dictionaries  # noqa: E402
from app.api import app  # noqa: E402
from app.config import settings  # noqa: E402
from app.extract import extract  # noqa: E402

FORM_7 = next((Path(__file__).parent.parent / "data/coi").glob("7. */*.PDF"), None)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", SecretStr("test-secret-" + "x" * 32))
    monkeypatch.setattr(settings, "jwt_jwks_url", None)
    monkeypatch.setattr(settings, "auth_password", SecretStr("tajne"))
    return TestClient(app)


def test_endpoint_needs_a_token(client):
    assert client.post("/api/dictionaries/proposals", files={"file": ("a.pdf", b"%PDF-1.7\n")}).status_code == 401


@pytest.mark.skipif(FORM_7 is None, reason="COI form packs not present (data/coi)")
def test_endpoint_returns_filtered_proposals_for_a_real_form(client, monkeypatch):
    monkeypatch.setattr(dictionaries, "transcribe", extract)  # the PDF's own text stands in for the Markdown
    monkeypatch.setattr(dictionaries, "ask_candidates", lambda text: [
        cand("Rasy psów", "Rasa psa", "Rasa psa:"),
        cand("Rasy psów (z wiedzy)", "Rasa", "Rasa psa:", ["amstaff", "pitbull"]),
        cand("Płcie", "Płeć", "Płeć:", ["pies", "suka"]),
    ])
    token = client.post("/api/auth/token", json={"username": settings.auth_username, "password": "tajne"}).json()
    r = client.post("/api/dictionaries/proposals", headers={"Authorization": f"Bearer {token['access_token']}"},
                    files={"file": (FORM_7.name, FORM_7.read_bytes())})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == FORM_7.name
    assert [p["name"] for p in body["proposals"]] == ["Rasy psów", "Płcie"]
    assert body["proposals"][0]["status"] == "do_uzupelnienia"
    assert len(body["dropped"]) == 1 and "amstaff" in body["dropped"][0]


def test_negated_pair_is_yes_no_and_street_is_address_block():
    text = "Bioodpady: [ ] są kompostowane [ ] nie są kompostowane\nUlica ____ Kod pocztowy ____"
    kept, dropped = filter_candidates([cand("Kompostowanie", "Bioodpady", "Bioodpady:", ["są kompostowane", "nie są kompostowane"]),
                                       cand("Ulice", "Ulica", "Ulica"), cand("Kody pocztowe", "Kod pocztowy", "Kod pocztowy")], text)
    assert kept == [] and "Tak/Nie" in dropped[0] and all("blok adresu" in d for d in dropped[1:])


def test_sentence_values_are_statements_not_items():
    long = "Oświadczam, że przydomowa oczyszczalnia ścieków objęta niniejszym zgłoszeniem oczyszcza ścieki z własnego gospodarstwa domowego lub rolnego"
    kept, dropped = filter_candidates([cand("Statusy", "Oświadczenie", "Oświadczam", [long, long + " w aglomeracji"])], long + " w aglomeracji")
    assert kept == [] and "całe zdania" in dropped[0]


def test_parenthetical_explanation_does_not_make_a_sentence():
    v = "nowa deklaracja (w przypadku zmiany danych będących podstawą ustalenia wysokości należnej opłaty za gospodarowanie odpadami komunalnymi)"
    text = f"Okoliczności: pierwsza deklaracja {v} korekta deklaracji"
    kept, _ = filter_candidates([cand("Okoliczności", "Okoliczności", "Okoliczności:", ["pierwsza deklaracja", v, "korekta deklaracji"])], text)
    assert len(kept) == 1
