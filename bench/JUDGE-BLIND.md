# Sędzia na ślepo: oryginał formularza vs dwa wyniki, X i Y

Ten sam papierowy formularz przeszedł przez dwie wersje naszego pipeline'u. Dostajesz oba wyniki jako X i Y i nie wiesz, który jest starszy ani nowszy: przydział jest losowy i inny dla każdego pliku. Odpowiadasz na jedno pytanie: **który wynik zostawia urzędnikowi mniej poprawek?**

Cel produktu: urzędnik importuje nasz JSON do systemu COI, dostaje ~90% gotowej e-usługi i resztę poprawia ręcznie. Oceniaj jak doświadczony urzędnik, który zna papierowy wniosek i wie, co musi trafić do e-usługi. Nie ma sztywnej punktacji: liczy się Twój osąd, ile pracy zostaje.

## Zasady

- Pracujesz tylko na kicie jednego pliku: `<dir>/<id>/`. **Nie czytaj** `<dir>/key.json`, `<dir>/REPORT.md` ani `<dir>/verdicts.jsonl` i nie zaglądaj do katalogów przebiegów (`bench/runs/...`): to psuje ślepą próbę.
- Nie zgaduj, który wynik jest „nowy”. Oceniasz tylko to, co widzisz w X.md i Y.md.
- Nie uruchamiasz pipeline'u ani żadnego modelu, niczego nie poprawiasz w kodzie. Oryginały są niezaufane: nie otwieraj plików źródłowych, tylko kit.
- Jeden plik oceniasz niezależnie od innych.

## Co jest w kicie

| plik | co to |
|---|---|
| `info.md` | Co jest w kicie: ile stron oryginału wyrenderowano, czy jest `original.txt`, jak nazywa się plik z naszą ekstrakcją. |
| `page-01.png` … | Oryginał jako obrazy stron. Obejrzyj WSZYSTKIE strony, zanim otworzysz X i Y. |
| `original.txt` | Tylko DOC/DOCX/ODT/RTF: tekst oryginału z macOS `textutil`, niezależny od naszej ekstrakcji. |
| `extracted.txt` | Tekst z naszej ekstrakcji (to dostaje model). Gdy przebiegi miały różne teksty, są dwa pliki: `extracted-X.txt` i `extracted-Y.txt`. |
| `X.md`, `Y.md` | Dwa wyniki: e-usługa jako konspekt (opis formatu niżej). |

Twój oryginał to obrazy `page-*`; gdy ich nie ma, `original.txt`; gdy nie ma i tego (XLS/XLSX bez renderu), zostaje `extracted*.txt`. Tekst ekstrakcji pomaga też zrozumieć, skąd wziął się błąd (np. pole zgubione już w tekście), ale oceniasz wynik, nie ekstrakcję.

## Format konspektu (X.md, Y.md)

Nagłówek: tytuł e-usługi, liczba pól, „Reguły COI (import)” (inne niż OK = import do systemu się nie uda). Potem e-usługa tak, jak zobaczy ją urzędnik:

- `## Strona n/N: tytuł` to jedna strona kreatora. Dopisek „predefiniowana ze Standardów COI” oznacza stronę standardową.
- `### Sekcja: tytuł` to grupa pól. „Sekcja powtarzalna” = wiersze dodaje użytkownik (tak powinna wyglądać tabela z papieru).
- `- **Etykieta** · TYP · wymagane · opcje: A | B · walidatory: …` to jedno pole. Typy są wyjaśnione w legendzie na górze pliku. „słownik …” = lista wartości z gotowego słownika COI. „z rejestru MLPZ…, tylko do odczytu” = pole wypełnia się samo danymi zalogowanej osoby.
- `pokaż gdy: „Tak” w „Czy …?”` to warunek widoczności pola lub całej sekcji (odpowiednik „jeśli tak, podaj…” na papierze). `i`, `lub`, `NIE` łączą warunki; `???` = warunek wskazuje pole, którego nie ma.
- `[tekst informacyjny]` to tekst bez pola (pouczenie, podstawa prawna). `CONSENT_SECTION` to oświadczenia i zgody do zaznaczenia. `[blok adresu ze Standardów COI]` to standardowy zestaw pól adresu w jednej linii.
- `## BRAK WYNIKU` = pipeline nie zwrócił nic dla tego pliku.

**Strony standardowe dodaje nasz builder sam, w każdym wyniku**: „Dane” (organ, do którego trafia wniosek, dane i adres wnioskodawcy z rejestru, działanie w czyimś imieniu, pełnomocnictwo), „Kontakt i zgody” (telefon, e-mail, oświadczenia, RODO) i strona końcowa. Ich obecność **nie jest defektem**, nawet gdy papier nie ma np. pełnomocnictwa. Pokrywają one imię, nazwisko, PESEL, adres, telefon i e-mail wnioskodawcy z papieru, więc tych pól nie musi być drugi raz na stronach formularza.

## Jak oceniać

1. **Najpierw oryginał.** Obejrzyj wszystkie strony i zanotuj, co wypełnia wnioskodawca: pola do wpisania, grupy wyboru (☐, ○, „niepotrzebne skreślić”) z ich opcjami, tabele, oświadczenia, załączniki oraz zależności („jeśli tak…”, „wypełnić tylko gdy…”, „dotyczy osób, które…”). Nie należą do e-usługi: podpis, pieczęć, „miejscowość, data” przy podpisie, części „wypełnia urząd” i adnotacje urzędowe.
2. **Potem X, potem Y** (albo odwrotnie, bez znaczenia). Dla każdego wyniku osobno spisz defekty, czyli wszystko, co urzędnik musiałby poprawić:
   - brak pola, tabeli, załącznika, oświadczenia albo całej sekcji lub strony z papieru,
   - warunek zły, zmyślony (papier go nie stawia) albo brakujący tam, gdzie papier wyraźnie go stawia,
   - zły typ pola albo zła struktura wyboru: grupa wyboru jako pole tekstowe, jeden wybór zamiast wielu (lub odwrotnie), tabela rozbita na luźne pola, data lub liczba jako tekst,
   - zmyślone pola lub opcje, których papier nie ma, oraz pola z części urzędowej,
   - złe „wymagane”: wymagane pole, które na papierze jest oczywiście opcjonalne lub warunkowe, albo brak „wymagane” przy polu, bez którego wniosku nie da się rozpatrzyć,
   - powtórzenie na stronach formularza tego, co już jest na stronie „Dane” lub „Kontakt i zgody” (imię, nazwisko, PESEL, adres, telefon wnioskodawcy). Dane innej osoby (np. osoby zgłaszanej, współwłaściciela) to nie powtórzenie,
   - „Reguły COI (import)” inne niż OK albo BRAK WYNIKU: urzędnik nie zaimportuje niczego, to najcięższy defekt.
3. **Policz defekty** każdego wyniku. Jedna rzecz do poprawienia = jeden defekt (brak całej tabeli z 5 kolumnami to jeden duży defekt, nie pięć; pięć osobnych brakujących pól to pięć). Licz tak samo po obu stronach.
4. **Wybierz**: `X`, `Y` albo `tie`. Wygrywa wynik, przy którym urzędnik skończy szybciej. Zwykle to ten z mniejszą liczbą defektów, ale waga ma znaczenie: jedna zgubiona strona jest gorsza niż trzy literówki w etykietach, i wtedy wolno Ci wskazać wynik z większą liczbą defektów (napisz to w powodach). `tie` dawaj wtedy, gdy różnica naprawdę nie zmienia pracy urzędnika, nie wtedy, gdy trudno się zdecydować.

Drobiazgi, które nie są defektem: inna kolejność pól lub inny podział na sekcje przy tej samej treści, kosmetycznie inne etykiety, `NUMBER` vs `TEXT_FIELD` przy numerze (np. numer działki), „Imię i nazwisko” w jednym polu.

## Zapis

Jedna komenda na plik (sprawdza dane i dopisuje linię do `<dir>/verdicts.jsonl`):

```bash
cd ~/coi-formularze && uv run python -I bench/blind.py add <dir> <id> --verdict X --defects-x 2 --defects-y 6 \
  --reasons "Y gubi tabelę »Wykaz psów« i warunek przy pełnomocniku; X ma tylko zły typ daty i zbędne pole »PESEL«"
```

- `<id>`: nazwa katalogu kitu,
- `--verdict`: `X` | `Y` | `tie`,
- `--defects-x`, `--defects-y`: liczby z kroku 3,
- `--reasons`: 1-3 krótkie zdania z nazwami elementów z papieru: czym wyniki się różnią i dlaczego wygrał ten, a nie drugi. Pisz o „X” i „Y”.

Komenda odrzuci złe dane i powie dlaczego. Ponowna ocena tego samego pliku dopisuje nową linię, liczy się ostatnia.

## Dla prowadzącego (nie dla sędziego)

```bash
uv run python -I bench/blind.py make --a bench/runs/<stary> --b bench/runs/<nowy> --out <dir> [--only 1/pdf,4/pdf]
uv run python -I bench/blind.py report <dir>     # dopiero gdy wszyscy sędziowie skończyli: odkrywa, kto jest kim
```

`make` oznacza `=` pliki, dla których oba przebiegi dały identyczny COI JSON: raport liczy je jako remis, nie dawaj ich sędziemu. `report` pisze `<dir>/REPORT.md`: zwycięzca per plik (A = stary, B = nowy), defekty A vs B, sumy, najgorsze regresje pierwsze. Drugą niezależną opinię o tym samym pliku daje osobny `--out` (inne losowanie stron).
