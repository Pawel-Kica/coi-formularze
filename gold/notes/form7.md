# Form 7: zezwolenie na psy ras agresywnych (Wrocław) - gold plan notes

- Applicant: person only (paper asks for name, address, phone; no PESEL, no company data). Proxy allowed (Standards default, paper does not forbid). Phone goes to the generated contact page. Addressee "Urząd Miejski Wrocławia, Wydział Środowiska".
- Dropped: "Nr sprawy /wypełnia organ/", "Miejsce na pieczęć Urzędu", "Wrocław, dnia", signature, RODO clause (code-generated consent page links to it), the legal-basis sentence.
- "hodowanie/utrzymywanie *" (niepotrzebne skreślić) -> RADIO "Hodowanie / Utrzymywanie". "charta/chartów lub mieszańca charta/mieszańców chartów" -> CHECKBOX "Chart rasowy / Mieszaniec charta": singular vs plural is carried by the number of dog sections, purebred vs mixed can both apply with several dogs.
- "w przypadku więcej niż jednego psa pobrać odpowiednią liczbę załączników z tabelą B" -> table B is the REPEATING section "Pies".
- "Pochodzenie psa (metryka**, numer rodowodu**, imię, nazwisko i adres poprzedniego właściciela)" split into 5 fields; metryka and rodowód optional (** = Opcjonalnie), previous owner's name/address required because the paper marks only those two as optional. A COI mentor might make the previous owner optional (own-bred dog has none).
- "Sposób oznakowania psa / nr ident.: mikroprocesor ___ / tatuaż ___ / brak" -> RADIO + number field shown only for the chosen method.
- "Data szczepienia (w przypadku jego braku)" is ambiguous on paper (braku czego: szczepienia, zaświadczenia?). Kept as optional DATEPICKER labelled as the rabies vaccination date, no guessed hint.
- Statement 2 "w załączeniu kopia dla każdego psa" -> required FILE_UPLOADER inside the repeating dog section (one copy per dog), statement text itself stays a declaration.
- "Adres miejsca utrzymywania psa/psów" split into address fields (one place for all dogs, as on paper, not per dog). "Opis miejsca i warunków" -> TEXTAREA with a short example hint.
- Fee "Opłata skarbowa – 82 zł" + account number -> TEXT_BLOCK + required proof-of-payment upload on the last subject page.
- 3 "Oświadczam, że..." -> statements, rewritten in present tense and gender-neutral ("zaszczepiono" impersonal form avoided).
