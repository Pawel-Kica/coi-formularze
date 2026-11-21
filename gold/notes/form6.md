# Form 6: Wniosek o wydanie zezwolenia na zajęcie pasa drogowego w celu prowadzenia robót (Urząd Miasta i Gminy Miasteczko)

- 3 subject pages: Miejsce i zakres robót / Termin i wykonawca robót / Załączniki.
- Addressee from the header "Urząd Miasta i Gminy Miasteczko". The RODO text names Daleszyce (copy-paste leftover on the paper); header wins.
- Applicant: person + entity ("Imię i nazwisko lub nazwa firmy"), `entity_ids: []` (paper asks for none, Standards default), `proxy: true` (paper mentions a pełnomocnik). Phone and e-mail -> contact page.
- "nazwa drogi oraz jej nr" split: Numer drogi gminnej (required) + Nazwa drogi (optional, not every gmina road has a name). Mentor may want both required.
- "rodzaj robót i sposób wykonania" split: Rodzaj robót (text) + Sposób wykonania (textarea).
- Area grid (4 categories x 2 rows of długość x szerokość = razem m2) -> one REPEATING section: RADIO category (4 options, values from paper) + Długość, Szerokość, Powierzchnia. Then "Całkowita powierzchnia zajęcia" NUMBER. Judgment call: repeating section instead of 8 fixed rows; a builder could later compute razem and total automatically.
- Termin: two DATEPICKERs + "Liczba dni" NUMBER (paper has it; derivable, kept because the decision/fee uses it).
- Wykonawca: one text field "nazwa firmy albo imię i nazwisko" (not split into person/company, it is a third party with no ID fields on paper). Osoba odpowiedzialna: Imię, Nazwisko (split), Adres zamieszkania (one field, paper has one "zam." line), Numer telefonu.
- Attachments: 3 always-required uploaders with scale hints. "Projekt organizacji ruchu, jeżeli ... wpływa na ruch" and "Informacja o zabezpieczeniu robót, jeżeli nie jest wymagany projekt" are mutually exclusive -> one synthesized RADIO "Czy zajęcie pasa drogowego wpływa na ruch drogowy?" showing one uploader for Tak, the other for Nie.
- Proxy attachment + 17 zł fee -> generated proxy/stamp-duty sections, not in pages. No main fee on the paper (zajęcie fee comes later in the decision), so no fee TEXT_BLOCK.
- "Wyrażam zgodę na przetwarzanie..." -> statement (required, as on paper). A mentor may drop it: processing here rests on a legal obligation, the consent page already links the RODO clause. RODO text itself is not put on the form (Standards).
