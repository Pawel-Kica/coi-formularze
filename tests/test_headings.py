"""Paper headings of pages and sections (app/headings.py): nearest heading above a section's quotes, its page."""

from app.builder import build_with_evidence
from app.headings import _heading, add_headings, section_headings
from app.plan import Page, Plan, PlanField, Section
from app.sources import Paper

# form 8 shape: one label ("Miejscowość") printed in two parts, a heading broken over lines, a section with none
TEXT = """DEKLARACJA O WYSOKOŚCI OPŁATY
Wstęp bez nagłówka
1. Uwagi
B.3 ADRES ZAMIESZKANIA
11. Miejscowość
C. OKOLICZNOŚCI UZASADNIAJĄCE ZŁOŻENIE DEKLARACJI
25. Okoliczności
D. DANE ADRESOWE NIERUCHOMOŚCI, NA KTÓREJ
POWSTAJĄ ODPADY
26. Miejscowość
F. DANE DO KONTAKTU (pola nieobowiązkowe)
70. Telefon
"""


def f(id_: str, quote: str) -> PlanField:
    return PlanField(id=id_, label=quote, type="TEXT_FIELD", quote=quote)


def plan() -> Plan:
    return Plan(title="Deklaracja o wysokości opłaty", addressee="", pages=[
        Page(title="Wstęp", sections=[Section(id="s0", title="Uwagi", fields=[f("f0", "Uwagi")])]),
        Page(title="Okoliczności", sections=[
            Section(id="s1", title="Okoliczności", fields=[f("f1", "Okoliczności")]),
            Section(id="s2", title="Nieruchomość", fields=[f("f2", "Miejscowość")]),
        ]),
        Page(title="Kontakt", sections=[Section(id="s3", title="Kontakt", fields=[f("f3", "Telefon")])]),
    ])


def test_heading_lines():
    assert _heading("## C. OKOLICZNOŚCI") == "C. OKOLICZNOŚCI"
    assert _heading("| **D.1 ADRES NIERUCHOMOŚCI** | | |") == "D.1 ADRES NIERUCHOMOŚCI"
    assert _heading("**F. DANE DO KONTAKTU (pola nieobowiązkowe)**") == "F. DANE DO KONTAKTU (pola nieobowiązkowe)"
    assert _heading("II. DANE WNIOSKODAWCY") == "II. DANE WNIOSKODAWCY"
    for line in ["25. Okoliczności", "GDYNIA", "| PESEL | NIP |", "A. Kowalski", "**Pouczenie:**"]:
        assert _heading(line) is None, line


def test_section_headings():
    p = plan()
    coi, ev = build_with_evidence(p)
    found = section_headings(Paper.from_text(TEXT), coi, ev)
    assert "s0" not in found  # only the form title above it
    assert found["s1"][0] == "C. OKOLICZNOŚCI UZASADNIAJĄCE ZŁOŻENIE DEKLARACJI"
    # "Miejscowość" after the previous section, and the heading joined across its two lines
    assert found["s2"][0] == "D. DANE ADRESOWE NIERUCHOMOŚCI, NA KTÓREJ POWSTAJĄ ODPADY"
    assert found["s3"][0] == "F. DANE DO KONTAKTU (pola nieobowiązkowe)"


def test_heading_runs_on_into_next_field():
    assert _heading("E.4 RAZEM WYSOKOŚĆ MIESIĘCZNEJ OPŁATY 69. SUMA poz. 51 lub 67 oraz 68") == "E.4 RAZEM WYSOKOŚĆ MIESIĘCZNEJ OPŁATY"


def test_heading_page_from_the_paper_lines():
    p = plan()
    coi, ev = build_with_evidence(p)
    pages = ["# Tytuł\nB.3 ADRES ZAMIESZKANIA\n1. Uwagi", "C. OKOLICZNOŚCI\n25. Okoliczności\nD. NIERUCHOMOŚĆ\n26. Miejscowość\n70. Telefon"]
    add_headings(ev, coi, Paper.from_pages(pages))
    tabs = {t["title"]: t for t in coi["tabs"]}
    assert (ev[tabs["Wstęp"]["id"]]["heading"], ev[tabs["Wstęp"]["id"]]["heading_page"]) == ("B.3 ADRES ZAMIESZKANIA", 1)
    assert (ev[tabs["Okoliczności"]["id"]]["heading"], ev[tabs["Okoliczności"]["id"]]["heading_page"]) == ("C. OKOLICZNOŚCI", 2)


def test_add_headings_marks_sections_and_first_section_pages():
    p = plan()
    coi, ev = build_with_evidence(p)
    add_headings(ev, coi, Paper.from_text(TEXT))
    tabs = {t["title"]: t for t in coi["tabs"]}
    okol = tabs["Okoliczności"]
    first, second = (q["field"]["id"] for q in okol["questions"])
    assert ev[okol["id"]]["heading"] == ev[first]["heading"] == "C. OKOLICZNOŚCI UZASADNIAJĄCE ZŁOŻENIE DEKLARACJI"
    assert ev[second]["heading"].startswith("D. DANE ADRESOWE")
    assert ev[first]["heading_page"] is None
    assert "heading" not in ev[tabs["Wstęp"]["id"]]
    assert all("heading" not in ev[t["id"]] for t in coi["tabs"] if t["title"] in ("Dane", "Kontakt i zgody"))
