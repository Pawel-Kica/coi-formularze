# Jak doszliśmy do tej architektury

Szczegóły do skrótu w README („Jak doszliśmy do tej architektury”). Dziś każdy plik czyta Qwen3.5-397B-A17B (obraz strony → Markdown), a plan formularza pisze GLM-5.2. Oba modele mają otwarte wagi i działają na Eden AI. Każdy krok niżej rozstrzygnął pomiar na 9 formularzach COI z zadania, w kilku formatach (PDF, skan, Word). Notatki robocze z pomiarów są w `bench/MODELS.md`.

## Jak mierzyliśmy

- `gold=` porównuje plan modelu z wzorcowym planem formularza (`gold/plans/formN.json`, uzasadnienia w `gold/notes/`). To średnia z pięciu miar: F1 pól, zgodność typów, zgodność opcji, odzysk warunków i zgodność strony „Dane” (kto składa wniosek). 1,0 = jak wzorzec. Wzorce przygotował zespół, więc `gold=` mierzy zgodność z naszymi decyzjami projektowymi. Dlatego ostateczną miarą jest liczba poprawek urzędnika liczona przez sędziów (README, „Wyniki”).
- Do tego: czy JSON powstał, ile problemów z regułami COI, czas i koszt.
- Uruchomienie: `uv run python -m bench.run_all` (woła modele na Edenie, więc kosztuje), ocena jednego pliku: `uv run python bench/score.py plan <nasz.json> <wzorzec.json>`.
- Przebiegi nie są powtarzalne co do bitu: małe formularze potrafią się różnić o 0,2 `gold=`. Zysk liczyliśmy dopiero wtedy, gdy było go widać na kilku dokumentach albo w dwóch przebiegach.

## 1. Start: lokalny qwen3 30B w Ollamie (porzucony)

Zaczęliśmy od modelu na laptopie: `qwen3:30b-a3b-instruct-2507-q4_K_M` w Ollamie, czytający tekst z naszej ekstrakcji (PDFium, parsery Worda i arkuszy, Tesseract dla skanów). 26 plików: 26 poprawnych JSON-ów, 0 problemów z regułami COI, średnio `gold=` 0,759 (PDF 0,795, skan PNG 0,724, DOCX 0,758).

| Formularz | PDF | skan PNG | DOCX | Czas PDF / PNG / DOCX (s) |
|---|---|---|---|---|
| 1 | 0,978 | 0,978 | 0,960 | 20 / 74 / 31 |
| 2 | 0,971 | 1,000 | 0,843 | 84 / 69 / 75 |
| 3 | 0,629 | 0,589 | 0,567 | 43 / 32 / 41 |
| 4 | 0,753 | 0,753 | 0,708 | 70 / 66 / 69 |
| 5 | 0,960 | 0,803 | 0,803 | 36 / 34 / 40 |
| 6 | 0,736 | 0,736 | 0,722 | 124 / 121 / 113 |
| 7 | 0,939 | 0,563 | brak DOCX | 110 / 79 |
| 8 | 0,587 | 0,540 | 0,713 | 400 / 411 / 304 |
| 9 | 0,603 | 0,557 | 0,752 | 422 / 414 / 285 |

- Długie formularze 8 i 9 (do 16 stron) zajmowały 285-422 s na plik.
- Lokalny model wizyjny `qwen3-vl:30b-a3b-instruct` przegrał na formularzach 1, 4, 5 i 7: średnio 0,77 z obrazami stron wobec 0,84 modelu tekstowego. Wymyślał pola i zamieniał wybór na pytania Tak/Nie.
- Reguły po modelu (filtr warunków, naprawy w `app/repair.py`) sprawdziliśmy na 18 prawdziwych pustych formularzach urzędowych spoza zadania. Dwóch sędziów AI porównywało wynik bez reguł i z regułami w losowej kolejności, nie wiedząc, która wersja jest która: z regułami wygrywa 9 razy, bez nich 1 raz, 8 remisów. Błędy do poprawienia przez urzędnika: 70 → 56 (-20%). Na tym zestawie reguły też rozwijaliśmy, więc to nie jest zestaw kontrolny.

Gdzie był sufit? Ten sam tekst i te same prompty, ale plan pisał mocniejszy model zamknięty (pomiar diagnostyczny, nie produkt; ten model pomagał też pisać wzorce, więc jego wynik jest raczej zawyżony):

| Przypadek | qwen3 30B | Mocniejszy model |
|---|---|---|
| Formularz 7, skan (tekst z OCR) | 0,563 | 0,968 |
| Formularz 8, PDF | 0,587 | 0,845 |
| Formularz 9, PDF | 0,578 | 0,858 |

Qwen na formularzu 9 to tu wcześniejszy przebieg niż w tabeli wyżej (0,603), stąd inna liczba.

Wąskim gardłem był model, nie odczyt ani kod. To nas przeniosło na większe modele z otwartymi wagami.

## 2. Sito modeli na Edenie

Wzięliśmy tylko modele z otwartymi wagami i licencją bez copyleft. Pierwsze sito: 9 formularzy, PDF i skany, modele dostawały tekst formularza, nie obrazy, 0,43 $ za całość.
- `gemma-4-31B-it` najlepszy: `gold=` 0,81, F1 warunków 0,75;
- `Qwen3.8-27B` dwa razy szybszy, ale 0,79 i słabszy na skanach;
- Mistral Small 4 i Qwen3.6 słabsze;
- odpadły Llama 3.3 (licencja) i mistral-ocr (zamknięte wagi), a darmowa trasa `google/` przez limity zapytań (22% odpowiedzi 429).

Potem doszedł duży model wizyjny `Qwen3.5-397B-A17B` (MoE, 17 mld aktywnych parametrów). Na nim rozstrzygnęliśmy, jak czytać plik.

## 3. Jak czytać plik: tekst (A) czy obrazy stron (B)

A = tekst z naszej ekstrakcji i Tesseracta. B = obraz każdej strony przepisany do Markdown przez Qwen3.5. Ten sam model planował w obu ramionach, średnia z 2 przebiegów.

| Plik | A: tekst | B: obrazy stron |
|---|---|---|
| Formularz 3, PDF | 0,693 | 0,696 |
| Formularz 3, skan PNG | 0,555 | 0,675 |
| Formularz 7, PDF | 0,614 | 0,713 |
| Formularz 7, skan PNG | 0,579 | **0,891** |
| Średnio | 0,610 (61 s na plik) | **0,744** (75 s na plik) |

- Obrazy wygrywają na skanach. Na PDF z warstwą tekstu zysk jest mniejszy, a na formularzu 3 to remis. Długi formularz 8 (PDF, 1 przebieg) też remisuje: A 0,64 (268 s, 0,055 $), B 0,65 (279 s, 0,084 $).
- Wszystkie 16 JSON-ów przeszło reguły COI.

Decyzja: zostaje tylko B. Jedna ścieżka dla każdego formatu, bez osobnego kodu na skany. Tesseract już nie czyta treści, zostały mu ramki słów do „Źródła”.

## 4. Planer: ten sam Markdown, cztery modele

Formularz 3 w trzech formatach, ścieżka B, ten sam Markdown dla każdego planera, bez trybu myślenia, średnia z 2 przebiegów.

| Planer | PDF | JPEG | DOCX | Średnio | s na plan | $ za 3 pliki |
|---|---|---|---|---|---|---|
| **GLM-5.2** | 0,967 | 0,950 | 0,933 | **0,950** | 12 | 0,028 |
| DeepSeek V4 Pro | 0,900 | 0,933 | 0,950 | 0,928 | 15 | 0,046 |
| Mistral Large 3 | 0,808 | 0,793 | 0,450 | 0,684 | 12 | 0,020 |
| Qwen3.5 | 0,507 | 0,292 | 0,637 | 0,478 | 123 | 0,028 |

- GLM-5.2 ma najlepszy plan, jest 10 razy szybszy od Qwen3.5 w roli planera i kosztuje tyle samo. Odczyt tych 3 plików przez Qwen3.5 kosztuje 0,006 $.
- `gpt-oss-120b` nie dał wyniku (błędy dostawców 503 i 400).

Decyzja: GLM-5.2 planuje, Qwen3.5 czyta. GLM-5.2 nie czyta obrazów, więc podział ról wynika też z samych modeli.

## 5. GLM-5.2 na najtrudniejszych formularzach

Formularz 7 to skan z sekcją powtarzaną dla każdego psa, 8 to deklaracja z tabelą wyliczeń, a 9 to 16-stronicowy wniosek „3 w 1”. Pierwszy pomiar (1 przebieg) i górna granica z mocniejszym modelem na tekście z ekstrakcji:

| Przypadek | `gold=` | F1 pól | Opcje | Warunki odzysk / precyzja |
|---|---|---|---|---|
| 7 JPG, GLM-5.2 | 0,846 | 0,82 | 0,50 | 1,00 / 0,67 |
| 7, mocniejszy model | 0,968 | 0,96 | 0,92 | 1,00 / 1,00 |
| 8 PDF, GLM-5.2 | 0,737 | 0,81 | 0,67 | 0,27 / 0,33 |
| 8 PDF, mocniejszy model | 0,845 | 0,87 | 0,86 | 0,53 / 0,53 |
| 9 DOCX, GLM-5.2 | 0,578 | 0,57 | 0,66 | 0,00 / 0,00 |
| 9 PDF, mocniejszy model | 0,858 | 0,68 | 0,86 | 0,80 / 0,47 |

- Odczyt trwał 28 / 65 / 43 s, plan 41 / 41 / 43 s. Trzy formularze kosztowały razem 0,119 $, około 0,04 $ za plik.
- Najwięcej brakowało w warunkach długich formularzy. Na formularzu 9 model zadawał pytanie o rodzaj wniosku innymi słowami niż wzorzec, więc ocena nie łączyła jego warunków z wzorcowymi, a na formularzu 8 w połowie przebiegów gubił wybór roli składającego (właściciel, współwłaściciel, najemca), od którego zależy reszta.
- Tryb myślenia nie pomógł: GLM-5.2 z myśleniem na formularzu 9 zużył cały limit 16 000 tokenów odpowiedzi już w pierwszym przebiegu i nie oddał planu, dwa razy. Myślenie zostaje wyłączone.

Poprawka (jeden akapit promptu: rola składającego i rodzaj wniosku to dane sprawy; kod wyrzuca pytania Tak/Nie, których żaden warunek nie używa; ocena łączy pytanie wyboru po liście opcji). Ten sam zapisany Markdown, tylko planer od nowa, średnia z 2 przebiegów:

| Formularz | 3 PDF | 7 JPG | 8 PDF | 9 DOCX | Średnio |
|---|---|---|---|---|---|
| Przed | 0,933 | 0,954 | 0,704 | 0,640 | 0,808 |
| Po | 0,933 | 0,937 | 0,764 | 0,732 | **0,842** |

Rola składającego na formularzu 8 i rodzaj wniosku na formularzu 9 pojawiły się w 6 z 6 przebiegów, wcześniej w 2 z 4.

## 6. Od pliku do JSON-a przez działające API

Formularz 7 (skan JPG) przez API w końcowej konfiguracji: `POST /api/forms/convert?evidence=1` dał JSON po 28 s, `POST /api/forms/check` 0 problemów z regułami COI. W środku: 4 kroki, 16 sekcji, 58 pól (w tym 7 list, 6 pól wyboru, 4 załączniki), sekcja powtarzalna dla kolejnych psów, 27 warunków pokazania. Propozycje słowników z tego samego odczytu stron: „Rasy psów”, „Płci psów”, „Sposoby oznakowania zwierząt”.

Obraz Dockera (z LibreOffice) wstaje z samym `EDENAI_API_KEY` w `.env`, odpowiada na `/health` i zamienia DOCX formularza 9 na 24 obrazy stron w 1,3 s, bez wywołania modelu (LibreOffice łamie ten DOCX na więcej stron niż 16 w PDF-ie).
