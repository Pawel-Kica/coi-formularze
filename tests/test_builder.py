"""Builder: gold Plans of forms 1 and 5 reproduce the COI reference JSONs."""

import glob
import importlib.util
import json
from pathlib import Path

import pytest

from app.builder import build, detect_validators
from app.builder.standard import is_organ
from app.plan import Applicant, Condition, EntityId, Page, Plan, PlanField, Section, Statement
from app.rules import check

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("bench_score", ROOT / "bench" / "score.py")
score = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(score)


def gold(name: str) -> Plan:
    return Plan.model_validate_json((ROOT / "gold" / "plans" / f"{name}.json").read_text())


def reference(n: int) -> dict:
    found = glob.glob(str(ROOT / "data" / "coi" / f"{n}.*" / "konfiguracja*.json"))
    if not found:
        pytest.skip("COI reference JSONs not available (data/ symlink)")
    return json.loads(Path(found[0]).read_text())


def all_fields(coi: dict):
    for t in coi["tabs"]:
        for q in t["questions"]:
            yield t, q["field"]
            for c in q["field"]["repeatingSectionGrid"]:
                yield t, c["field"]


def by_tech(coi: dict) -> dict:
    return {f["techName"]: f for _, f in all_fields(coi)}


@pytest.mark.parametrize("n", [1, 5])
def test_gold_reproduces_reference(n):
    coi = build(gold(f"form{n}"))
    assert check(coi) == []
    rep = score.score_coi(coi, reference(n))
    for kind in ("tabs", "sections", "fields", "dependencies"):
        assert rep["recall"][kind] >= 0.95, rep["summary"]


@pytest.mark.parametrize("path", sorted((ROOT / "gold" / "plans").glob("*.json")), ids=lambda p: p.stem)
def test_every_gold_plan_builds_valid_json(path):
    coi = build(Plan.model_validate_json(path.read_text()))
    assert check(coi) == []


def test_deterministic():
    plan = gold("form5")
    a = json.dumps(build(plan), ensure_ascii=False)
    b = json.dumps(build(Plan.model_validate_json(plan.model_dump_json())), ensure_ascii=False)
    assert a == b


def test_tech_names_and_ids_unique():
    coi = build(gold("form5"))
    techs = [f["techName"] for _, f in all_fields(coi)]
    ids = [f["id"] for _, f in all_fields(coi)] + [t["id"] for t in coi["tabs"]]
    assert len(techs) == len(set(techs))
    assert len(ids) == len(set(ids))


def test_reference_specific_switches():
    one, five = by_tech(build(gold("form1"))), by_tech(build(gold("form5")))
    assert one["nazwa_organu"]["defaultValue"] == "Wójt Gminy Elbląg"
    assert one["nazwa_organu"]["readOnly"] is True
    assert five["nazwa_urzedu"]["defaultValue"] == "Urząd Gminy Ulan-Majorat"
    # Read-only but must hold a value: REQUIRED (gold/coi/NOTES.md).
    assert one["nazwa_organu"]["validation"] == five["nazwa_urzedu"]["validation"] == ["REQUIRED"]
    # Standards win over the trimmed references: Twoje dane always has PESEL, all from MLPZ, read-only.
    assert one["imie"]["defaultValue"] == "MLPZ.name" and one["numer_pesel"]["defaultValue"] == "MLPZ.pesel"
    assert five["numer_pesel"]["defaultValue"] == "MLPZ.pesel" and five["numer_pesel"]["readOnly"] is True
    # Own address: the whole block from MLPZ, read-only (Kraj and TERYT too, plus a Polish Miejscowość).
    for tech, src in [("kraj", "country"), ("wojewodztwo", "voivodeship"), ("miejscowosc", "place"),
                      ("kod_pocztowy", "postCode"), ("kod_pocztowy_zagranica", "postCode")]:
        assert one[tech]["readOnly"] is True and one[tech]["defaultValue"] == f"MLPZ.{src}"
        assert one[tech]["defaultValueType"] == "EXTERNAL_SYSTEM"
    # Other people's addresses stay editable with the Standards defaults.
    for suffix in ("_inna_osoba", "_siedziba"):
        assert one[f"kraj{suffix}"]["readOnly"] is False and one[f"kraj{suffix}"]["defaultValue"] == ["Polska"]
        assert one[f"wojewodztwo{suffix}"]["defaultValue"] == [] and f"miejscowosc{suffix}" not in one
    assert five["numer_pesel"]["validation"] == ["REQUIRED", "PESEL"]
    assert {"nip", "regon", "numer_krs"} <= one.keys()
    assert "nip" in five and "regon" not in five
    assert "„Wniosek o stwierdzenie nadpłaty podatku – opłata" in five["oplata_info_1"]["description"]


def test_jsonata_shape():
    f = by_tech(build(gold("form5")))["nazwa_podatku"]
    assert f["states"] == [{
        "type": "SHOW",
        "query": "(\"Zaliczyć na poczet przyszłego zobowiązania podatkowego\" in tabs['Informacje o nadpłacie' "
                 "in title].questions['rozliczenie_nadplaty' in field.techName].field.repeatingSectionGrid"
                 "['wybierz_co_mamy_zrobic_z_nadplata' in field.techName].field.__userAnswer)",
    }]  # fmt: skip
    ulica = by_tech(build(gold("form5")))["ulica_zagranica"]["states"][0]["query"]
    assert ulica.startswith('$not("Polska" in tabs[\'Dane\' in title]')


def _plan(fields, sections=None, applicant=None, statements=()):
    secs = sections or [Section(id="s1", title="Sprawa", fields=fields)]
    return Plan(title="Wniosek o test", addressee="Burmistrz Miasta Testowo",
                applicant=applicant or Applicant(), pages=[Page(title="Sprawa", sections=secs)],
                statements=list(statements))


def test_repeating_section_condition_uses_row_index():
    fields = [
        PlanField(id="f1", label="Rasa", type="RADIO", options=["Inna", "Amstaff"], quote="rasa"),
        PlanField(id="f2", label="Jaka rasa", type="TEXT_FIELD", quote="jaka",
                  show_if=Condition(field="f1", options=["inna"], quote="jeśli inna")),
    ]
    coi = build(_plan(fields, [Section(id="s1", title="Pies", repeating=True, fields=fields)]))
    assert check(coi) == []
    q = by_tech(coi)["jaka_rasa"]["states"][0]["query"]
    assert "[$repeatingSectionIndex]['rasa' in fieldTechName].answers" in q
    assert '"Inna"' in q  # option snapped to the real name


def test_robust_to_bad_plan():
    fields = [
        PlanField(id="f1", label="Kraj urodzenia", type="SELECT", quote="kraj"),
        PlanField(id="f2", label="Rodzaj", type="RADIO", quote="rodzaj"),  # no options
        PlanField(id="f3", label="Ulica", type="TEXT_FIELD", quote="ulica",
                  show_if=Condition(field="nope", options=["x"], quote="q")),
        PlanField(id="f4", label="Opis", type="TEXT_BLOCK", description="Wypełnij uważnie.", quote="opis"),
    ]
    coi = build(_plan(fields, applicant=Applicant(person=False, entity=True, proxy=False),
                      statements=[Statement(text="Oświadczam, że dane są prawdziwe.", quote="Oświadczam")]))
    assert check(coi) == []
    t = by_tech(coi)
    assert t["kraj_urodzenia"]["dictionaryName"] == "Kraje"
    assert t["rodzaj"]["type"] == "TEXT_FIELD"
    assert t["ulica_2"]["states"] == []
    assert t["info_opis"]["title"] == "Opis" and "<p>Wypełnij uważnie.</p>" in t["info_opis"]["description"]
    assert "pelnomocnictwo" not in t and "podmiot_wniosku" not in t  # entity only, no proxy
    assert t["potwierdzenie_ze_jestes_reprezentantem"]["states"] == []
    consents = t["zgoda"]["consentSectionConfig"]["consents"]
    assert len(consents) == 2 and t["zgody_i_oswiadczenia"]["title"] == "Zgody i oświadczenia"


def test_person_without_proxy_has_no_podmiot_section():
    coi = build(_plan([PlanField(id="f1", label="Uwagi", type="TEXTAREA", quote="uwagi")],
                      applicant=Applicant(person=True, proxy=False)))
    assert check(coi) == []
    assert "podmiot_wniosku" not in by_tech(coi) and "dane_innej_osoby" not in by_tech(coi)


def test_entity_ids_default_and_explicit():
    a = Applicant(person=True, entity=True, entity_ids=[EntityId.REGON])
    t = by_tech(build(_plan([PlanField(id="f1", label="Uwagi", type="TEXTAREA", quote="u")], applicant=a)))
    assert "regon" in t and "nip" not in t and "numer_krs" not in t


def test_helpers():
    assert is_organ("Wójt Gminy Elbląg") and is_organ("Do Burmistrza Miasta") and is_organ("Rada Miasta")
    assert not is_organ("Urząd Gminy Ulan-Majorat")
    assert detect_validators("Numer PESEL") == ["PESEL"]
    assert detect_validators("Kod pocztowy") == ["ZIP_CODE"]
    assert detect_validators("Adres e-mail") == ["EMAIL"]
    assert detect_validators("Seria i numer dowodu osobistego") == ["SERIAL_NUMBER_ID_CARD"]
