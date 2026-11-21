# Form 8: deklaracja o opłacie za odpady (Gdynia) - gold plan notes

- Applicant: person + entity (wspólnota, spółdzielnia, "Nazwa pełna", NIP/REGON), PESEL yes, proxy yes (objaśnienie 13). B.1.2-4, B.3 address and H.74-75 signer name live on the code-built Dane page.
- Dropped: legal basis, "Składający"/"Termin składania" header, A. organ address (-> addressee), F.70-71 (contact page), H date/signature, I. "Adnotacja organu", G.73.3 (Dane page proxy upload), RODO text, objaśnienia 1-5.
- B.1.1 six roles -> SELECT in plain language ("Właściciel lub współwłaściciel", "Współwłaściciel razem z małżonkiem (wspólność majątkowa)", "Najemca lub dzierżawca", ...). Role 6 -> TEXTAREA shown for it.
- Role 2 shows "Dane współmałżonka" (Imię, Nazwisko, PESEL + TEXT_BLOCK "Deklarację podpisuje także współmałżonek.", information only, no upload) and "Adres zamieszkania współmałżonka" (`address: true`).
- B.3.16 działka and B.4 "(wypełnić tylko ... inny niż w poz. B.3)" have no trigger on paper -> synthesized Tak/Nie RADIOs. B.4 is an `address: true` section; the paper's free-text Kraj/Województwo/Gmina fields are dropped (the Standards block has Kraj/TERYT dictionaries).
- D.1 property address is an `address: true` section too; "Gdynia" and objaśnienie 7 (one declaration per property) are in its description.
- C.25 four circumstances -> RADIO; each date -> DATEPICKER shown for its option ("Data zmiany danych" serves both nowa deklaracja and korekta).
- D.3 moved before D.2 so it can drive it: D.2 only for multi-unit buildings, plus a synthesized RADIO from objaśnienie 8.
- D.4.35 compost "Oświadczam..." is a RADIO shown for "jednorodzinny" (it switches the fee table), not a consent statement.
- E.1 vs E.2: E.2 shown when compost = Tak, E.1 otherwise (negate). Bracket labels follow the paper without overlap: "do 45 m²", "powyżej 45 do 60 m²", "powyżej 60 do 80 m²", "powyżej 80 m²". Rates sit in the hints. All counts, amounts, subtotals and the E.4 total are optional NUMBERs (derivable); exemption-amount quotes are "50. Wysokość" / "66. Wysokość" (robust to pdftotext without -layout).
- D.3.34 TAK -> E.3 amount (required, copied from załącznik A/B) and required "Plik z załącznikiem A lub B do deklaracji".
- Pouczenie (declaration = executable title) kept as a TEXT_BLOCK next to the total. F.72 e-mail correspondence consent -> optional statement.
