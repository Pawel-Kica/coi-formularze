"""Editor support: evidence map (COI element id -> paper quote) on real plans, POST /api/forms/check,
convert ?evidence=1, and rules problems located on the element they belong to."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import pipeline
from app.api import app
from app.builder import build, build_with_evidence
from app.config import settings
from app.plan import Plan
from app.problems import explain, locate
from app.rules import check, query_atoms

ROOT = Path(__file__).resolve().parents[1]
GOLD = sorted((ROOT / "gold" / "plans").glob("form*.json"))
# Saved runs (gitignored, present on the dev Mac): real model plans next to the COI JSON they produced.
SAVED = sorted((ROOT / "bench" / "runs" / "drive-baseline").glob("*.plan.json"))
SECRET = "test-secret-" + "x" * 32


def plan_of(path: Path) -> Plan:
    try:
        return Plan.model_validate_json(path.read_text())
    except ValueError:  # a run still writing it
        pytest.skip(f"{path.name} is not a complete plan")


def elements(coi: dict):
    """(tab, element) for every tab, section and field id in the COI JSON."""
    for t in coi["tabs"]:
        yield t, t
        for q in t["questions"]:
            yield t, q["field"]
            for c in q["field"]["repeatingSectionGrid"]:
                yield t, c["field"]


def plan_fields(plan: Plan):
    for p in plan.pages:
        for s in p.sections:
            for f in s.fields:
                yield s, f


@pytest.mark.parametrize("path", GOLD + SAVED, ids=lambda p: p.name)
def test_evidence_covers_every_element_and_every_quote(path):
    plan = plan_of(path)
    coi, evidence = build_with_evidence(plan)
    assert coi == build(plan)  # the COI JSON itself does not change
    ids = [e["id"] for _, e in elements(coi)]
    assert list(evidence) == ids  # one entry per element, in COI order
    assert {e["source"] for e in evidence.values()} <= {"paper", "model", "code"}

    by_plan_id = {e["plan_id"]: (i, e) for i, e in evidence.items() if "plan_id" in e}
    for s, f in plan_fields(plan):
        if s.address and not s.repeating:  # the Standards address block replaced these lines
            _, sec = by_plan_id[s.id]
            assert f.quote in sec["quote"]
            continue
        coi_id, entry = by_plan_id[f.id]
        assert entry == {**entry, "source": "paper", "quote": f.quote, "page": None}
        field = next(e for _, e in elements(coi) if e["id"] == coi_id)
        # a condition quote only when the builder really made a SHOW condition from it
        assert ("condition_quote" in entry) == bool(field["states"])
        if field["states"]:
            assert entry["condition_quote"] == f.show_if.quote


@pytest.mark.parametrize("path", GOLD + SAVED, ids=lambda p: p.name)
def test_evidence_of_standard_parts(path):
    plan = plan_of(path)
    coi, evidence = build_with_evidence(plan)
    standard = {coi["tabs"][0]["id"], coi["tabs"][-2]["id"], coi["tabs"][-1]["id"]}  # Dane, Kontakt i zgody, finish
    for t, e in elements(coi):
        if t["id"] in standard and e["id"] in standard:
            assert evidence[e["id"]]["source"] == "code"
    for t in coi["tabs"][1:-2]:
        assert evidence[t["id"]]["source"] == "model"
    addressee = coi["tabs"][0]["questions"][0]["field"]["repeatingSectionGrid"][0]["field"]
    expected = plan.addressee_quote if plan.addressee.strip() and plan.addressee_quote.strip() else None
    assert evidence[addressee["id"]]["quote"] == expected
    consent = next(e for _, e in elements(coi) if e["type"] == "CONSENT_SECTION")
    quotes = evidence[consent["id"]]["consent_quotes"]
    assert len(quotes) == len(consent["consentSectionConfig"]["consents"])
    assert quotes == [None] + [s.quote for s in plan.statements]


# --- rules problems for the clerk ---


@pytest.fixture
def coi():
    """Gold form 5: page 2 has a RADIO ("rozliczenie nadpłaty") with two fields shown on its options."""
    return build(plan_of(ROOT / "gold" / "plans" / "form5.json"))


def find(coi, pred):
    return next(e for _, e in elements(coi) if pred(e))


def test_problems_point_at_the_element(coi):
    assert explain(coi, check(coi)) == []
    sec = find(coi, lambda e: e.get("techName") == "rozliczenie_nadplaty")
    radio = sec["repeatingSectionGrid"][0]["field"]
    sec["title"] = " "
    radio["options"].append(dict(radio["options"][0]))
    by_id = {p["id"]: p for p in explain(coi, check(coi))}
    assert by_id[sec["id"]]["message"] == "Sekcja nie ma tytułu."
    assert by_id[radio["id"]]["message"] == f"Opcja „{radio['options'][0]['name']}” występuje dwa razy."
    assert by_id[radio["id"]]["detail"].endswith("repeated")  # the rules line stays available


def test_condition_on_a_deleted_option_or_field_is_shown_on_the_dependent(coi):
    sec = find(coi, lambda e: e.get("techName") == "rozliczenie_nadplaty")
    radio, dependent = (c["field"] for c in sec["repeatingSectionGrid"][:2])
    (option, _tab, _sec, trigger), *_ = query_atoms(dependent["states"][0]["query"])[0]
    assert trigger == radio["techName"]

    radio["options"] = [o for o in radio["options"] if o["name"] != option]
    found = [(p["id"], p["message"]) for p in explain(coi, check(coi))]
    assert (dependent["id"], f"Warunek używa opcji „{option}”, której to pole już nie ma.") in found

    sec["repeatingSectionGrid"] = sec["repeatingSectionGrid"][1:]
    found = [(p["id"], p["message"]) for p in explain(coi, check(coi))]
    assert (dependent["id"], "Warunek zależy od pola, którego już nie ma.") in found


def test_tab_level_problems(coi):
    page = coi["tabs"][1]
    page["title"] = "Dane"  # queries on page 2 now point at a missing tab too; only the title problem matters here
    problems = explain(coi, check(coi))
    assert {"id": page["id"], "message": "Dwie strony mają tytuł „Dane”. Tytuły stron muszą się różnić."} in [
        {k: p[k] for k in ("id", "message")} for p in problems]
    coi["title"] = ""
    assert {"id": None, "message": "E-usługa nie ma tytułu."} in [
        {k: p[k] for k in ("id", "message")} for p in explain(coi, check(coi))]


def test_locate_longest_tab_title_first():
    coi = {"tabs": [{"id": "a", "title": "Dane", "questions": []},
                    {"id": "b", "title": "Dane 'x'", "questions": [{"field": {"id": "s", "techName": "sek"}}]}]}
    assert locate(coi, "tab 'Dane 'x'' / sek: section has no title") == "s"
    assert locate(coi, "tab 'Dane': page has no fields") == "a"
    assert locate(coi, "flow link 1: source/target is not a tab id") is None


# --- API ---


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", SecretStr(SECRET))
    monkeypatch.setattr(settings, "jwt_jwks_url", None)
    monkeypatch.setattr(settings, "auth_username", "urzednik")
    monkeypatch.setattr(settings, "auth_password", SecretStr("tajne"))
    return TestClient(app)


@pytest.fixture
def auth(client):
    token = client.post("/api/auth/token", json={"username": "urzednik", "password": "tajne"}).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


def test_check_endpoint(client, auth, coi):
    assert client.post("/api/forms/check", json=coi).status_code == 401
    assert client.post("/api/forms/check", json=coi, headers=auth).json() == {"problems": []}
    coi["tabs"][1]["questions"][0]["field"]["title"] = ""
    problems = client.post("/api/forms/check", json=coi, headers=auth).json()["problems"]
    assert [p["message"] for p in problems] == ["Sekcja nie ma tytułu."]
    assert problems[0]["id"] == coi["tabs"][1]["questions"][0]["field"]["id"]
    broken = client.post("/api/forms/check", json={"tabs": "nie"}, headers=auth).json()["problems"]
    assert broken and all(p["id"] is None for p in broken)
    assert client.post("/api/forms/check", content=b"[1]", headers=auth).status_code == 422


def test_convert_evidence_is_opt_in(client, auth, monkeypatch):
    plan = plan_of(ROOT / "gold" / "plans" / "form1.json")
    coi, evidence = build_with_evidence(plan)
    monkeypatch.setattr(pipeline, "convert", lambda data, filename: coi)
    monkeypatch.setattr(pipeline, "convert_with_evidence", lambda data, filename: (coi, evidence))
    pdf = b"%PDF-1.7\n%test\n"
    plain = client.post("/api/forms/convert", headers=auth, files={"file": ("a.pdf", pdf)})
    assert plain.status_code == 200 and plain.json() == coi  # the product rule: bare COI JSON by default
    wrapped = client.post("/api/forms/convert?evidence=1", headers=auth, files={"file": ("a.pdf", pdf)})
    assert wrapped.json() == {"coi": coi, "evidence": evidence}


def test_editor_script_served(client):
    response = client.get("/editor.js")
    assert response.status_code == 200 and "javascript" in response.headers["content-type"]


def test_blank_form_passes_the_rules_and_belongs_to_the_clerk(client, auth):
    """'Zacznij od pustego formularza': the standard pages around one page to build by hand, nothing from paper."""
    assert client.get("/api/forms/blank").status_code == 401
    data = client.get("/api/forms/blank", headers=auth).json()
    coi, evidence = data["coi"], data["evidence"]
    assert [t["title"] for t in coi["tabs"]] == ["Dane", "Nowa strona", "Kontakt i zgody", "Strona końcowa"]
    assert check(coi) == []
    assert {el["id"] for _, el in elements(coi)} == set(evidence)
    page = coi["tabs"][1]
    own = [page, page["questions"][0]["field"], page["questions"][0]["field"]["repeatingSectionGrid"][0]["field"]]
    assert all(evidence[el["id"]]["source"] == "clerk" for el in own)  # the editor renames their techNames
    assert {e["source"] for e in evidence.values()} == {"clerk", "code"}
