"""Bonus I on subject pages: PESEL registry links only for fields plainly about the applicant."""

import pytest

from app.builder import build
from app.builder.registry import PERMANENT_ADDRESS, address_source, pesel_attribute
from app.plan import Applicant, Page, Plan, PlanField, Section
from app.rules import check

SOLO = Applicant(person=True, proxy=False)  # the logged-in person always files in their own name


def _f(label, type_="TEXT_FIELD", **kw):
    return PlanField(id="f1", label=label, type=type_, quote="q", **kw)


def _attr(label, type_="TEXT_FIELD", section="Dane wnioskodawcy", page="Sprawa", repeating=False, **kw):
    f = _f(label, type_, **kw)
    return pesel_attribute(f, Section(id="s1", title=section, repeating=repeating, fields=[f]), page)


@pytest.mark.parametrize("label,type_,section,attr", [
    ("Data urodzenia", "DATEPICKER", "Dane wnioskodawcy", "PESEL.birthDate"),
    ("Data urodzenia wnioskodawcy", "DATEPICKER", "Informacje", "PESEL.birthDate"),
    ("Twoja data urodzenia", "DATEPICKER", "Informacje", "PESEL.birthDate"),
    ("Miejsce urodzenia", "TEXT_FIELD", "Twoje dane", "PESEL.birthPlace"),
    ("Obywatelstwo", "TEXT_FIELD", "Dane osoby wnioskującej", "PESEL.citizenship"),
    ("Imię ojca", "TEXT_FIELD", "Dane wnioskodawcy", "PESEL.fatherName"),
    ("Nazwisko rodowe:", "TEXT_FIELD", "Składający deklarację", "PESEL.familyName"),
    ("Drugie imię", "TEXT_FIELD", "Dane wnioskodawcy (wypełnia wnioskodawca):", "PESEL.secondName"),
])  # fmt: skip
def test_positive_labels(label, type_, section, attr):
    assert _attr(label, type_, section) == attr


@pytest.mark.parametrize("label,type_,section,why", [
    ("Data urodzenia dziecka", "DATEPICKER", "Dane wnioskodawcy", "another person in the label"),
    ("Data urodzenia", "DATEPICKER", "Dane małżonka wnioskodawcy", "another person in the section"),
    ("Data urodzenia", "DATEPICKER", "Dane kandydata", "candidate, no applicant mention"),
    ("Data urodzenia", "DATEPICKER", "Dane osoby zgłaszającej", "no applicant mention"),
    ("Data urodzenia", "DATEPICKER", "Informacje", "no applicant mention"),
    ("Data urodzenia psa", "DATEPICKER", "Twoje dane", "the dog"),
    ("Data urodzenia", "DATEPICKER", "Właściciel psa (wnioskodawca)", "owner named separately"),
    ("Data urodzenia", "DATEPICKER", "Pełnomocnik wnioskodawcy", "proxy"),
    ("Data urodzenia", "DATEPICKER", "Członek gospodarstwa wnioskodawcy", "household member"),
    ("Data urodzenia", "TEXT_FIELD", "Dane wnioskodawcy", "type differs from the register (Datepicker)"),
    ("Data i miejsce urodzenia", "TEXT_FIELD", "Dane wnioskodawcy", "compound label"),
    ("Imię ojca dziecka", "TEXT_FIELD", "Dane wnioskodawcy", "child's father"),
    ("Nazwisko rodowe matki", "TEXT_FIELD", "Dane wnioskodawcy", "mother, not the applicant"),
    ("Numer PESEL", "TEXT_FIELD", "Dane wnioskodawcy", "already on the Dane page"),
    ("Imię", "TEXT_FIELD", "Dane wnioskodawcy", "already on the Dane page"),
    ("Płeć", "RADIO", "Dane wnioskodawcy", "choice on paper, text in the register"),
    ("Numer telefonu", "TEXT_FIELD", "Dane wnioskodawcy", "phone stays a plain text field"),
    ("Narodowość", "TEXT_FIELD", "Dane wnioskodawcy", "often ethnicity, not citizenship"),
    # council round 1: the applicant word only somewhere in the heading is not enough
    ("Data urodzenia", "DATEPICKER", "Dane drugiego wnioskodawcy", "second applicant"),
    ("Data urodzenia", "DATEPICKER", "Wnioskodawca 2", "second applicant"),
    ("Data urodzenia", "DATEPICKER", "Dane wnioskodawców", "several applicants"),
    ("Data urodzenia", "DATEPICKER", "Dane współwnioskodawcy", "co-applicant"),
    ("Data urodzenia", "DATEPICKER", "Dane osoby reprezentowanej przez wnioskodawcę", "represented person"),
    ("Data urodzenia", "DATEPICKER", "Osoba upoważniona przez wnioskodawcę", "authorised person"),
    ("Data urodzenia", "DATEPICKER", "Dane osoby innej niż wnioskodawca", "another person"),
    ("Data urodzenia", "DATEPICKER", "Dane osoby, w imieniu której działa wnioskodawca", "principal"),
    ("Data urodzenia", "DATEPICKER", "Dane osoby zgłoszonej przez wnioskodawcę", "reported person"),
    ("Data urodzenia wnioskodawcy", "DATEPICKER", "Dane pacjenta", "label says applicant, section another"),
])  # fmt: skip
def test_negative_labels(label, type_, section, why):
    opts = {"options": ["Kobieta", "Mężczyzna"]} if type_ == "RADIO" else {}
    assert _attr(label, type_, section, **opts) is None, why


def test_negative_context_outside_section_title():
    assert _attr("Data urodzenia", "DATEPICKER", page="Dane dziecka") is None
    # the page title never names the applicant on its own
    for section in ("Dane osoby niepełnosprawnej", "Dane studenta", "Dane osobowe", "Osoba uprawniona"):
        assert _attr("Data urodzenia", "DATEPICKER", section=section, page="Dane wnioskodawcy") is None
    assert _attr("Data urodzenia", "DATEPICKER", page="Wnioskodawca 2") is None
    assert _attr("Data urodzenia", "DATEPICKER",
                 description="Wypełnia osoba, której dane dotyczą, jeżeli nie jest wnioskodawcą") is None
    assert _attr("Data urodzenia", "DATEPICKER", description="Wpisz datę urodzenia współmałżonka") is None
    assert _attr("Data urodzenia", "DATEPICKER", repeating=True) is None  # a repeating section never


def _addr(title, plan_title="Wniosek o stypendium", page="Sprawa", labels=("Ulica", "Kod pocztowy")):
    fields = [PlanField(id=f"a{i}", label=lab, type="TEXT_FIELD", quote="q") for i, lab in enumerate(labels)]
    return address_source(Section(id="s1", title=title, address=True, fields=fields), page, plan_title)


def test_address_source():
    assert _addr("Adres zameldowania wnioskodawcy") == PERMANENT_ADDRESS
    assert _addr("Twój adres stałego zameldowania") == PERMANENT_ADDRESS
    assert _addr("Adres zameldowania na pobyt stały wnioskodawcy (jeśli posiada)") == PERMANENT_ADDRESS
    assert _addr("Adres zameldowania na pobyt tymczasowy wnioskodawcy") is None  # usually empty
    assert _addr("Adres zameldowania na pobyt stały lub czasowy wnioskodawcy") is None
    assert _addr("Adres zameldowania lub zamieszkania wnioskodawcy") is None
    assert _addr("Adres ostatniego zameldowania wnioskodawcy") is None
    assert _addr("Adres zameldowania drugiego wnioskodawcy") is None
    assert _addr("Adres zameldowania osoby reprezentowanej przez wnioskodawcę") is None
    assert _addr("Adres zameldowania kandydata") is None
    assert _addr("Adres zameldowania") is None  # whose? no applicant mention
    assert _addr("Adres zameldowania", page="Dane wnioskodawcy") is None  # the page never counts
    assert _addr("Adres zamieszkania wnioskodawcy") is None  # residence is not registration (MLPZ on Dane)
    assert _addr("Adres zameldowania dziecka wnioskodawcy") is None
    assert _addr("Nowy adres zameldowania wnioskodawcy") is None
    assert _addr("Adres zameldowania wnioskodawcy", plan_title="Zgłoszenie pobytu stałego") is None
    assert _addr("Adres zameldowania wnioskodawcy", labels=("Ulica (małżonka)",)) is None


def _plan(sections, applicant):
    return Plan(title="Wniosek o test", addressee="Burmistrz Miasta Testowo", applicant=applicant,
                pages=[Page(title="Sprawa", sections=sections)])


def _fields(coi):
    for t in coi["tabs"]:
        for q in t["questions"]:
            yield q["field"]
            for c in q["field"]["repeatingSectionGrid"]:
                yield c["field"]


def _by_tech(coi):
    return {f["techName"]: f for f in _fields(coi)}


def _self_plan(applicant):
    return _plan([
        Section(id="s1", title="Dane wnioskodawcy", fields=[
            _f("Data urodzenia", "DATEPICKER", required=True),
            PlanField(id="f2", label="Data urodzenia dziecka", type="DATEPICKER", quote="q2"),
        ]),
        Section(id="s2", title="Adres zameldowania wnioskodawcy", address=True, fields=[
            PlanField(id="a1", label="Ulica", type="TEXT_FIELD", quote="ulica"),
        ]),
    ], applicant)


def test_solo_filer_gets_read_only_registry_fields():
    coi = build(_self_plan(SOLO))
    assert check(coi) == []
    t = _by_tech(coi)
    birth = t["data_urodzenia"]
    assert (birth["readOnly"], birth["defaultValue"], birth["defaultValueType"]) == (
        True, "PESEL.birthDate", "EXTERNAL_SYSTEM")
    assert birth["validation"] == [] and birth["states"] == []  # may be empty in the register
    child = t["data_urodzenia_dziecka"]
    assert not child["readOnly"] and "defaultValue" not in child
    assert "data_urodzenia_inna_osoba" not in t  # no twin: nobody else can be the applicant
    addr = t["adres_zameldowania_wnioskodawcy"]
    sources = {f["techName"]: f.get("defaultValue") for f in (c["field"] for c in addr["repeatingSectionGrid"])}
    assert sources["ulica_zameldowania_wnioskodawcy"] == "PESEL_ADDRESS.permanentAddressStreet"
    assert sources["kraj_zameldowania_wnioskodawcy"] == ["Polska"]  # no country in the register
    assert not any(k.endswith("_zagranica") for k in sources)
    assert all(c["field"]["readOnly"] for c in addr["repeatingSectionGrid"])
    fed = [c["field"] for c in addr["repeatingSectionGrid"] if c["field"].get("defaultValueType")]
    assert fed and not any("REQUIRED" in f["validation"] for f in fed)


def test_proxy_possible_adds_editable_twin_for_other_filers():
    coi = build(_self_plan(Applicant(person=True, proxy=True)))
    assert check(coi) == []  # JSONata of both variants evaluates
    t = _by_tech(coi)
    fed, twin = t["data_urodzenia"], t["data_urodzenia_inna_osoba"]
    assert fed["readOnly"] and fed["defaultValue"] == "PESEL.birthDate"
    assert not twin["readOnly"] and "defaultValue" not in twin and twin["validation"] == ["REQUIRED"]
    own = '("Swoim" in tabs[\'Dane\' in title].questions[\'podmiot_wniosku\' in field.techName]'
    assert fed["states"][0]["query"].startswith(own)
    assert twin["states"][0]["query"].startswith("$not" + own)
    addr_twin = t["adres_zameldowania_wnioskodawcy_inna_osoba"]
    assert addr_twin["states"][0]["query"].startswith("$not" + own)
    assert not any(c["field"]["readOnly"] for c in addr_twin["repeatingSectionGrid"])


def test_entity_only_filer_never_gets_registry():
    coi = build(_self_plan(Applicant(person=False, entity=True, proxy=False)))
    assert check(coi) == []
    pages = coi["tabs"][1:-2]
    assert not any(f.get("defaultValueType") == "EXTERNAL_SYSTEM" for p in pages
                   for f in _fields({"tabs": [p]}))
