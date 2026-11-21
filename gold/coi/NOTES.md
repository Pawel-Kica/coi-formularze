# gold/coi: COI references with validation added by hand

`form1.json` and `form5.json` are exact copies of the COI reference JSONs
(`data/coi/{1,5}. */konfiguracja_form_*.json`); only `validation` differs (test:
`tests/test_gold_coi.py`). COI mentors (21.11): the references are illustrative and
have `validation: []` everywhere, validation must be in the JSON. Added by hand by
Claude from the COI Standards (`HackNation - Standardy i dobre praktyki projektowania
formularzy.pdf`), the paper forms and COI's visualisation docx (screenshots: only
"Numer telefonu" and "Adres e-mail" carry "(opcjonalnie)"), not from our builder.
Decisions to review by Paweł. Allowed validators: `app/rules.py` VALIDATORS.

Used by `bench/run_all.py` (`walid=`, F1 of (field, validator) pairs on fields matched
by `score_coi`) and as the `ref=` reference (same structure, same `ref=` value).

Rules applied:
- REQUIRED = the Standards, the paper or the law need a value. Read-only fields that
  must hold a value (organ/urząd name, imię, nazwisko, PESEL) get REQUIRED too.
- Format validator from what the field holds (PESEL, NIP, REGON, ZIP_CODE, EMAIL).
- Standards fields with a "Brak ..." escape checkbox (Ulica, Numer lokalu, foreign
  Kod pocztowy and Ulica) stay without REQUIRED: the checkbox has no key in the JSON
  schema, so REQUIRED would block anyone without a street / flat number / postcode.
- `validation: {}` in the originals (form 1: numer_krs, ulica_siedziba_zagranica,
  nazwisko_inna_osoba) normalized to a list.
- Sections, TEXT_BLOCK and CONSENT_SECTION: `[]` (consents carry their own `required`).

## Both forms, page "Dane"
- Nazwa organu (1) / Nazwa urzędu (5): REQUIRED. Read-only default, but the application must name its addressee.
- Wybierz, w czyim imieniu wnioskujesz: REQUIRED (Standards, Podmiot wniosku).
- Wybierz, jako kto wnioskujesz: REQUIRED (Standards; only visible for "Osoby prawnej...").
- Twoje dane / Imię, Nazwisko: REQUIRED (Standards; read-only from MLPZ/PESEL).
- Twoje dane / Numer PESEL (form 5 only, form 1 reference has none): REQUIRED, PESEL (Standards).
- Adres zamieszkania / Kraj: REQUIRED (Standards).
- Adres zamieszkania / Miejscowość (abroad): REQUIRED (Standards).
- Adres zamieszkania / TERYT (Polska): REQUIRED (Standards).
- Adres zamieszkania / Kod pocztowy (Polska): REQUIRED, ZIP_CODE (Standards).
- Adres zamieszkania / Numer budynku: REQUIRED (Standards).
- Adres zamieszkania / Ulica (Polska): no REQUIRED. Standards say required + "Brak nazwy ulicy" checkbox, which the JSON cannot express (form 5's own office, "Ulan-Majorat 57", has no street).
- Adres zamieszkania / Numer lokalu: no REQUIRED. Same, "Brak numeru lokalu" checkbox not expressible; most houses have no flat number.
- Adres zamieszkania / Kod pocztowy (abroad): no REQUIRED, no ZIP_CODE. Standards: "kod pocztowy bez walidacji", escape checkbox "Brak kodu pocztowego".
- Adres zamieszkania / Ulica (abroad): no REQUIRED (escape checkbox, as above).
- Adres siedziby, Adres zamieszkania innej osoby: same block, same calls as Adres zamieszkania.
- Dane podmiotu / Nazwa: REQUIRED (Standards).
- Dane podmiotu / NIP: REQUIRED, NIP (Standards).
- Dane podmiotu / REGON (form 1 only): REQUIRED, REGON (Standards).
- Dane podmiotu / Numer KRS (form 1 only): no validator. Standards: "pole opcjonalne" + a Pattern regex, which is not in the allowed list. The screenshot shows no "(opcjonalnie)"; the Standards win.
- Potwierdzenie, że jesteś reprezentantem / Plik z odpisem danych: REQUIRED. The section exists only to collect this proof; no "(opcjonalnie)" in the screenshot.
- Dane innej osoby / Imię, Nazwisko: REQUIRED (Standards).
- Pełnomocnictwo / file (form 1 "Dokument z potwierdzeniem...", form 5 "Plik dokumentu z pełnomocnictwem"): REQUIRED (Standards).
- Opłata skarbowa / Czy złożenie pełnomocnictwa wymaga opłaty skarbowej?: REQUIRED (Standards).
- Opłata skarbowa / Potwierdzenie przelewu (form 1) / Plik z potwierdzeniem przelewu (form 5): REQUIRED (Standards; shown only after "Tak").
- Opłata skarbowa za pełnomocnictwo / Potwierdzenie przelewu (_2): REQUIRED (Standards, wersja 2).

## Both forms, contact page
- Numer telefonu: no validator. Standards "pole nie jest wymagane"; no phone validator exists; form 1 paper footnote "Dane nieobowiązkowe".
- Adres e-mail: EMAIL only, no REQUIRED (Standards).

## Form 1, Informacje o budynku / Lokalizacja budynku
- Miejscowość: REQUIRED (paper, not marked optional).
- Numer ewidencyjny działki: REQUIRED (paper).
- Obręb: REQUIRED (paper).
- Ulica: no REQUIRED. Paper footnote 1) "Dane nieobowiązkowe" on "przy ulicy" (COI's screenshot adds "Brak nazwy ulicy", same outcome).

## Form 5, Informacje o nadpłacie
- Podatek, którego dotyczy nadpłata: REQUIRED (paper "z tytułu ... (rodzaj podatku)").
- Kwota: REQUIRED (paper "w kwocie").
- Uzasadnienie: REQUIRED (paper has an "Uzasadnienie" block; no "(opcjonalnie)" in the screenshot).
- Wybierz, co mamy zrobić z nadpłatą: REQUIRED (paper "Kwotę nadpłaty proszę: 1) ... 2) ...").
- Nazwa podatku (shown for "Zaliczyć..."): REQUIRED. Contested: the paper's line is needed to know which liability to credit; required only while visible.
- Numer rachunku (shown for "Zwrócić przelewem..."): REQUIRED. Contested like above: a transfer needs an account. No IBAN validator exists.
- Plik deklaracji podatkowej: REQUIRED. Contested: the paper pre-prints "w załączeniu: 1) korekta deklaracji" (art. 75 § 3 Ordynacji), but for taxes set by decision (e.g. property tax of a natural person) there is no declaration to correct.

## Model runs (qwen3:30b, `bench/run_all.py --forms 1,5`)
`walid=` only scores fields that `score_coi` matches. In the runs so far the model names
the subject page differently ("Informacje o nieruchomości", "Wniosek o stwierdzenie
nadpłaty podatku"), `score_coi` does not match that tab, so its fields count as missing
structure (`ref=`) and their validators as `unmatched`, not as `walid=` misses. Hand check
of those plan fields (`required` comes from the model):
- Form 1, Ulica: model `required: true`, gold no REQUIRED. Model limit (it ignores the paper's "Dane nieobowiązkowe" footnote).
- Form 1, extra "Czy chcesz podać adres ulicy?" radio and "Numer budynku": not in the reference (structure, not validation).
- Form 5, "Rodzaj podatku, na który ma zostać zaliczona nadpłata" and "Numer rachunku bankowego": model `required: false`, gold REQUIRED. Model limit (conditional fields left optional).
- All other subject fields: model REQUIRED = gold REQUIRED.
