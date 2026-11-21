# Form 7: zezwolenie na psy (Wrocław) - gold plan notes

- Paper mismatch: the title says "psów ras uznawanych za agresywne" but the body (legal basis, request, RODO purpose) is about charty and their mieszańce (Prawo łowieckie). The plan follows the title for the e-service name and the body for the content.
- Applicant: person only (name, address, phone; no PESEL). Proxy allowed (Standards default). Applicant address and phone come from the generated Dane/contact pages, so no applicant address section here.
- Dropped: "Nr sprawy", "Miejsce na pieczęć Urzędu", "Wrocław, dnia", signature, RODO clause, legal-basis sentence.
- "hodowanie/utrzymywanie *" -> RADIO; "charta/chartów lub mieszańca charta/mieszańców chartów" -> CHECKBOX "Chart rasowy / Mieszaniec charta".
- Table B ("w przypadku więcej niż jednego psa pobrać ... tabelą B") -> REPEATING section "Pies".
- "Pochodzenie psa": metryka and rodowód optional (** = Opcjonalnie); previous owner (Imię, Nazwisko, then Miejscowość, Kod pocztowy, Ulica, Numer budynku, Numer lokalu as plain TEXT_FIELDs inside the repeating section) all optional, "Wpisz, jeśli pies ma poprzedniego właściciela." (own-bred dogs have none).
- Oznakowanie -> RADIO + number field shown only for the chosen method. "Data szczepienia (w przypadku jego braku)" is ambiguous; optional DATEPICKER, no guessed hint.
- Statement 2 "w załączeniu kopia dla każdego psa" -> required "Plik z kopią dokumentu szczepienia" inside the dog section; the statement stays a declaration.
- "Adres miejsca utrzymywania psa/psów" (one place for all dogs) -> `address: true` section (Standards address block, council round 2: one address schema everywhere). "Opis miejsca i warunków" -> TEXTAREA, no invented example hint.
- Fee: section "Opłata za zezwolenie" (no exemption on paper) -> unconditional TEXT_BLOCK (82 zł, account number) + required "Plik z potwierdzeniem przelewu".
- 3 "Oświadczam, że..." -> statements, present tense, gender-neutral.
