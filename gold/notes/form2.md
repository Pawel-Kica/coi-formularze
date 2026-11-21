# Form 2: Wniosek o zwrot opłaty skarbowej (Urząd Gminy Stawiguda)

- One subject page "Informacje o opłacie", 3 sections (opłata, uzasadnienie, sposób zwrotu), like ref 5 "Informacje o nadpłacie".
- Applicant: person + entity ("Nazwa /Imię i nazwisko"), `entity_ids: [NIP]` because the paper asks only for NIP. No PESEL on paper, so `pesel: false`.
- 2.2 address, 2.3 contact data and 2.4 "Dane pełnomocnika" are dropped: Dane page and contact page are generated. `proxy: true` because the paper has a proxy block.
- 7. Załączniki (pełnomocnictwo + dowód opłaty za pełnomocnictwo) are proxy-only: covered by the generated Pełnomocnictwo / Opłata skarbowa sections, so no uploaders here.
- "Gotówką w kasie organu / Przelewem" (postaw x, one choice) -> RADIO. "Jeśli wybierzesz tę formę, wpisz poniżej nr rachunku i dane jego właściciela" -> "Numer rachunku" and "Właściciel rachunku" shown only for przelew (same pattern as ref 5).
- "Dane właściciela" kept as one text field (name or company name). A mentor might want owner address too ("dane" is vague); I did not invent it.
- "organu" rewritten to "urzędu" in the option and in "Nazwa urzędu, który otrzymał opłatę" (plain language, no impersonal "zapłacono").
- Wysokość opłaty -> NUMBER (zł in description), Data uiszczenia -> DATEPICKER.
- Dropped: instruction block (paper-only), "6. Podstawa prawna" (Standards: no legal refs), date and signature.
- No main-request fee here (the form is a refund request), so no fee TEXT_BLOCK.
