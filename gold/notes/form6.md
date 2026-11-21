# Form 6: Wniosek o wydanie zezwolenia na zajęcie pasa drogowego w celu prowadzenia robót (Urząd Miasta i Gminy Miasteczko)

- 3 subject pages: Miejsce i zakres robót / Termin i wykonawca robót / Załączniki.
- Addressee from the header "Urząd Miasta i Gminy Miasteczko". The RODO text names Daleszyce (copy-paste leftover on the paper); header wins.
- Applicant: person + entity ("Imię i nazwisko lub nazwa firmy"), `entity_ids: []` (Standards default), `proxy: true` (paper mentions a pełnomocnik). Phone and e-mail -> contact page.
- "nazwa drogi oraz jej nr" split: Numer drogi gminnej (required) + Nazwa drogi (optional). "rodzaj robót i sposób wykonania" split: Rodzaj robót + Sposób wykonania.
- Area grid (4 categories x 2 rows) -> one REPEATING section: RADIO category + Długość, Szerokość, Powierzchnia ("Długość × szerokość", optional, derivable). "Całkowita powierzchnia zajęcia" optional ("Suma wszystkich pozycji").
- Termin: two DATEPICKERs (end date quotes "do dnia") + "Liczba dni robót" optional (derivable from the dates).
- Wykonawca: one text field. Osoba odpowiedzialna: Imię, Nazwisko, Numer telefonu, then its own address section (`address: true`, the paper's single "zam." line) so the builder uses the Standards address block.
- Attachments: 3 required "Plik z/ze …" uploaders with scale hints; projekt organizacji ruchu vs informacja o zabezpieczeniu robót are mutually exclusive -> synthesized RADIO "Czy zajęcie pasa drogowego wpływa na ruch drogowy?" showing one uploader for Tak, the other for Nie.
- Proxy attachment + 17 zł fee -> generated proxy sections. No main fee on the paper (the zajęcie fee comes in the decision), so no fee section.
- No statements: the paper's RODO consent is dropped (code always generates the RODO consent; the paper's text even names Daleszyce).
