# Form 2: Wniosek o zwrot opłaty skarbowej (Urząd Gminy Stawiguda)

- One subject page "Informacje o opłacie", 2 sections: "Zapłacona opłata" (data, kwota, urząd, uzasadnienie in one section) and "Sposób zwrotu opłaty".
- Applicant: person + entity ("Nazwa /Imię i nazwisko"), `entity_ids: [NIP]` because the paper asks only for NIP. No PESEL on paper, so `pesel: false`.
- 2.2 address, 2.3 contact data and 2.4 "Dane pełnomocnika" are dropped: Dane page and contact page are generated. `proxy: true` because the paper has a proxy block.
- 7. Załączniki (pełnomocnictwo + dowód opłaty za pełnomocnictwo) are proxy-only: covered by the generated Pełnomocnictwo / proxy-fee sections, so no uploaders here.
- No section is titled "Opłata skarbowa": that name belongs to the generated proxy-fee section.
- "Gotówką w kasie organu / Przelewem" (postaw x, one choice) -> RADIO "Wybierz, jak chcesz otrzymać zwrot opłaty". "Jeśli wybierzesz tę formę, wpisz poniżej nr rachunku i dane jego właściciela" -> "Numer rachunku" and "Właściciel rachunku" shown only for przelew (same pattern as ref 5).
- "Dane właściciela" kept as one text field (name or company name); no owner address invented.
- "organu" rewritten to "urzędu" (plain language). Wysokość opłaty -> NUMBER (zł in description), Data uiszczenia -> DATEPICKER.
- Dropped: instruction block (paper-only), "6. Podstawa prawna" (Standards: no legal refs), date and signature.
- No main-request fee here (the form is a refund request), so no fee section.
