"""Paper headings of pages and sections (app/headings.py): nearest heading above a section's quotes, its page."""

from app.builder import build_with_evidence
from app.headings import _heading, add_headings, heading_page, section_headings
from app.plan import Page, Plan, PlanField, Section

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
    found = section_headings(plan(), TEXT)
    assert "s0" not in found  # only the form title above it
    assert found["s1"][0] == "C. OKOLICZNOŚCI UZASADNIAJĄCE ZŁOŻENIE DEKLARACJI"
    # "Miejscowość" after the previous section, and the heading joined across its two lines
    assert found["s2"][0] == "D. DANE ADRESOWE NIERUCHOMOŚCI, NA KTÓREJ POWSTAJĄ ODPADY"
    assert found["s3"][0] == "F. DANE DO KONTAKTU (pola nieobowiązkowe)"


def test_heading_page():
    pages = ["# Tytuł\nA. CZĘŚĆ PIERWSZA\nPole", "B. CZĘŚĆ DRUGA\nPole drugie"]
    text = "\n\n".join(pages)
    assert heading_page("B. CZĘŚĆ DRUGA", 3, text, pages) == 2  # exact by line count (PIPELINE=images)
    layers = ["A. CZĘŚĆ PIERWSZA x", "y B. CZĘŚĆ DRUGA"]
    assert heading_page("B. CZĘŚĆ DRUGA", 0, "other text", layers) == 2  # searched in PDF text layers
    assert heading_page("B. CZĘŚĆ DRUGA", 0, text, []) is None


def test_add_headings_marks_sections_and_first_section_pages():
    p = plan()
    coi, ev = build_with_evidence(p)
    add_headings(ev, coi, p, TEXT, [])
    tabs = {t["title"]: t for t in coi["tabs"]}
    okol = tabs["Okoliczności"]
    first, second = (q["field"]["id"] for q in okol["questions"])
    assert ev[okol["id"]]["heading"] == ev[first]["heading"] == "C. OKOLICZNOŚCI UZASADNIAJĄCE ZŁOŻENIE DEKLARACJI"
    assert ev[second]["heading"].startswith("D. DANE ADRESOWE")
    assert ev[first]["heading_page"] is None
    assert "heading" not in ev[tabs["Wstęp"]["id"]]
    assert all("heading" not in ev[t["id"]] for t in coi["tabs"] if t["title"] in ("Dane", "Kontakt i zgody"))
