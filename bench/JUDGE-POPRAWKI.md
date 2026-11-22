# Poprawki urzędnika: brief sędziego i przepis na panel

Metryka: **liczba poprawek urzędnika**. Sędzia widzi papierowy formularz i naszą e-usługę (COI JSON jako czytelny konspekt) i wypisuje każdą poprawkę, którą urzędnik musiałby zrobić w naszym edytorze, żeby e-usługa pytała o to, o co pyta papier. Panel = 3 sędziów, poprawka liczy się, gdy wypisało ją 2 z 3. Mniej = lepiej.

Część między znacznikami `brief` trafia do kitu jako `BRIEF.md` (to czyta sędzia). Reszta to przepis dla orkiestratora.

<!-- brief -->
# Brief sędziego: poprawki urzędnika

Urzędnik zaimportował naszą e-usługę i poprawia ją w edytorze, patrząc na papierowy formularz. Twoje zadanie: wypisać **każdą poprawkę, którą musi zrobić**, żeby e-usługa pytała o to samo, o co pyta papier. Jedna poprawka = jedna czynność w edytorze: dodać pole, usunąć pole, zmienić typ, poprawić opcje, dodać lub poprawić warunek pokazania, przełączyć „wymagane”.

## Co masz (tylko ten folder)
- `page-01.png`, `page-02.png`, ...: papier (formularz źródłowy). Obejrzyj WSZYSTKIE strony.
- `ours.md`: nasza e-usługa: strony → sekcje → pola (typ, wymagane, opcje, „pokaż gdy” = warunek pokazania). W nawiasach `...` id elementu (techName).
- Nie czytaj innych plików, nie uruchamiaj niczego poza zapisem wyniku. Oceniasz względem papieru, nie względem żadnego wzorca.

## Jak pracować
1. Spisz w notatkach elementy papieru, strona po stronie: każde pole do wpisania (linia kropek, kratki, rubryka), każdą grupę wyboru (☐, ○, „niepotrzebne skreślić”) z jej opcjami, każdą kolumnę tabeli, każdy wymieniony załącznik, każde oświadczenie do potwierdzenia („Oświadczam, że…”), każde „jeśli…, to…”.
2. Znajdź każdy element w `ours.md`. Potem przejdź `ours.md` element po elemencie i szukaj go na papierze.
3. Wypisz poprawki według kategorii poniżej.

## Kategorie (pole `category`)
- `missing_field`: element papieru, którego u nas nie ma. Brak całej tabeli lub sekcji = jedna linia na każde brakujące pole (kolumnę). Brakujące oświadczenie („Oświadczam…”) też tu.
- `invented_field`: pole na stronach formularza, którego papier nie ma, którego wnioskodawca nie wypełnia (część „wypełnia urząd”), które powtarza inne nasze pole, albo które pyta drugi raz o dane wnioskodawcy ze strony „Dane” (imię, nazwisko, PESEL, jego adres, telefon, e-mail). Zgoda w CONSENT_SECTION, której papier nie ma (poza RODO), też tu.
- `wrong_type`: typ wyraźnie nie pasuje i blokuje właściwą odpowiedź: data nie jako DATEPICKER, liczba lub data tam, gdzie trzeba tekstu, grupa wyboru jako pole tekstowe, załącznik nie jako FILE_UPLOADER, pole do wpisania jako tekst informacyjny (TEXT_BLOCK) albo tekst informacyjny jako pole, RADIO zamiast CHECKBOX (lub odwrotnie), gdy papier wyraźnie pozwala na wiele (jedną) odpowiedzi.
- `wrong_options`: grupa wyboru, której brakuje opcji z papieru, która ma opcję spoza papieru albo opcje wyraźnie złe. Jedna linia na grupę, w `fix` wszystkie zmiany opcji.
- `missing_condition`: papier mówi, że element wypełnia się tylko w jakimś przypadku („jeśli tak, podaj”, „wypełnić w przypadku…”, „dotyczy…”, „zaznaczyć, gdy…”), a u nas jest pokazany zawsze.
- `wrong_condition`: warunek jest, ale zły: inne pole lub inna opcja niż na papierze, albo ukrywa element, który papier każe wypełnić zawsze.
- `wrong_required`: „wymagane” wyraźnie złe: sedno wniosku (bez czego urząd nie rozpatrzy sprawy: o co wnioskodawca prosi, dane przedmiotu sprawy, załącznik, który papier każe dołączyć) jest opcjonalne, albo coś, co papier oznacza jako dobrowolne („jeśli posiada”, „nieobowiązkowo”, „fakultatywnie”), jest wymagane. Gdzie rozsądni ludzie mogą się różnić, nie licz.

Różne kategorie na jednym elemencie = osobne linie. Ta sama kategoria na jednym elemencie = jedna linia.

## Czego NIE liczysz
- Stylu, brzmienia etykiet i opisów, kolejności pól, podziału na strony i sekcje, literówek.
- Walidatorów (ich braku ani obecności).
- Stron standardowych, które buduje kod ze Standardów COI: „Dane” (urząd, w czyim imieniu, imię, nazwisko, PESEL, adres wnioskodawcy, inna osoba, pełnomocnictwo, opłata za pełnomocnictwo), „Kontakt i zgody” (telefon, e-mail, zgoda RODO), „Strona końcowa”. One pokrywają te dane z papieru: ich obecność tam to nie brak. Wyjątek: oświadczenia z papieru w CONSENT_SECTION oceniasz (brak = `missing_field`, nadmiar = `invented_field`).
- Tekstów informacyjnych (TEXT_BLOCK): ich brak lub nadmiar to nie poprawka (chyba że zastępują pole do wypełnienia: `wrong_type`).
- Podpisu, pieczęci, „miejscowość, data” przy podpisie, części dla urzędu, pouczeń, podstaw prawnych.
- Innego, ale działającego kształtu tego samego pytania: jedna grupa RADIO zamiast kilku pytań Tak/Nie (lub odwrotnie), tabela jako sekcja powtarzalna albo jako pola, adres jako blok adresu ze Standardów albo jako pola tekstowe, TEXT_FIELD zamiast TEXTAREA, NUMBER zamiast TEXT_FIELD dla liczby, SELECT zamiast RADIO.

## Zapis
Jedna linia JSON na poprawkę (JSONL), nic więcej w pliku; brak poprawek = pusty plik:

```
{"element": "`kolor_elewacji`", "category": "wrong_options", "fix": "dodać opcję „Biały”"}
{"element": "Data zakończenia umowy", "category": "missing_field", "fix": "dodać DATEPICKER w sekcji Umowa"}
```

- `element`: techName z `ours.md` w backtickach, gdy element u nas jest (`invented_field`, `wrong_*`, `missing_condition`; brak opcji = techName grupy). Gdy elementu u nas nie ma (`missing_field`): krótki dosłowny cytat z papieru (etykieta pola lub początek oświadczenia), nie techName sekcji.
- `category`: jedna z siedmiu powyżej.
- `fix`: co urzędnik zmienia, krótko.

Zapisz plik pod ścieżką z polecenia (nadpisz go) i odpowiedz jedną linią: `<N>: <liczba> poprawek`.
<!-- /brief -->

## Przepis (orkiestrator lub implementer)

Wszystko z `/tmp/loop-<name>` albo `~/coi-formularze`, bez sieci i bez Edena (sędziowie to subagenci, nie Eden).

1. **Kit.** Pliki z przebiegu `bench.run_all` (papier = PDF formularza z `data/coi/<N>. .../` dla każdego formatu):
   ```
   uv run python -I bench/poprawki.py kit /Users/main/coi-formularze/bench/runs/<run> --files 3/pdf,8/pdf --out /Users/main/coi-formularze/bench/runs/<run>/poprawki
   ```
   Jeden COI JSON (ramię porównania podejść, kalibracja): `... kit <plik.coi.json> --form 3 --out <kit_dir>`. Przebieg `bench.run_dir` (BIP, Drive): `--files <nazwa pliku>`, papier = sam plik źródłowy.
2. **Panel.** Na każdy folder kitu `<kit_dir>/<id>/` trzech sędziów AI równolegle (N=1 i N=2: dwa niezależne wywołania tego samego mocnego modelu, N=3: inny model), każdy jako osobny agent bez wspólnego kontekstu. Prompt (podstaw ścieżkę bezwzględną folderu i N):
   ```
   Jesteś sędzią „poprawek urzędnika”. Pracujesz tylko w folderze <KIT>. Przeczytaj <KIT>/BRIEF.md i wykonaj go: obejrzyj wszystkie <KIT>/page-*.png (papier) i przeczytaj <KIT>/ours.md (nasza e-usługa). Nie czytaj innych plików ani folderów i niczego nie uruchamiaj. Zapisz poprawki jako JSONL do <KIT>/judge-<N>.jsonl (nadpisz plik, narzędziem Write). Odpowiedz jedną linią: „<N>: <liczba> poprawek”.
   ```
3. **Liczenie.** `uv run python -I bench/poprawki.py count <kit_dir> [--run <przebieg run_all>]` → poprawki na plik (2 z 3) z listą, `<kit_dir>/count.json`; z `--run` także `<kit_dir>/TABLE.md` (formularz, plik, gold=, poprawki według kategorii). Najlepszy plik każdego formularza (najwyższe gold= przy 0 problemów z regułami COI): `uv run python -I bench/poprawki.py pick <przebieg run_all> --forms 2,3,4,6,7,8,9` (ostatnia linia to gotowe `--files`). Dopasowanie linii między sędziami: ta sama grupa kategorii (typ i opcje to jedna grupa, brak i zły warunek druga) i ten sam element (ten sam techName albo jeden to sekcja drugiego; inaczej rdzenie słów etykiety lub cytatu, 5 liter bez ogonków, krótszy w ≥ 60% zawarty w dłuższym), pary łączone od najpewniejszej, w grupie najwyżej jedna linia na sędziego.
4. **Kalibracja** (dowód, że sędziowie widzą błędy i ich nie zmyślają):
   ```
   uv run python -I bench/poprawki.py plant 8 --out <dir>/form8-planted.coi.json   # + form8-clean.coi.json, form8-planted.key.json
   uv run python -I bench/poprawki.py kit <dir>/form8-clean.coi.json --form 8 --out <dir>/kits
   uv run python -I bench/poprawki.py kit <dir>/form8-planted.coi.json --form 8 --out <dir>/kits
   # panel (krok 2) na obu folderach, potem:
   uv run python -I bench/poprawki.py calib <dir>/kits/8-form8-clean <dir>/kits/8-form8-planted --key <dir>/form8-planted.key.json
   ```
   found = wpisane błędy trafione przez policzone poprawki (jeden do jednego); invented = policzone poprawki na wersji z błędami, które nie są ani wpisanym błędem, ani policzoną poprawką na czystej wersji. Zalicza: found ≥ 80% wpisanych i invented ≤ 1.

Koszt: sędzia nie woła Edena; na plik 3 subagentów, każdy czyta tylko kit (obrazy stron + `ours.md` + `BRIEF.md`).

## Kalibracja (22.11, sędziowie jak w kroku 2)

| Formularz | Wpisane | Znalezione | Invented | Czysta wersja (policzone) | Wynik |
|---|---|---|---|---|---|
| 8 | 10 | 10 | 0 | 0 | zalicza |
| 7 | 10 | 9 | 0 | 0 | zalicza (ominięte: zdjęte „wymagane” przy potwierdzeniu przelewu, którego papier wprost nie wymaga) |
| 3 | 7 | 4 | 0 | 0 | nie zalicza z reguły, ale 3 ominięte błędy siedzą na elementach bez pokrycia w papierze (pytanie Tak/Nie o opłatę i załącznik potwierdzenia dodane przez wzorzec, warunek przy TEXT_BLOCK); na elementach z papieru 4 z 4 |

Brief nie był poprawiany po kalibracji. Po formularzu 3 `plant` przestał wpisywać błędy warunków w teksty informacyjne (brief ich nie ocenia); klucze 7 i 8 się nie zmieniły. Pliki: `bench/runs/loop-judge-calib/` (klucze, kity, linie sędziów, `count.json`, `calib.json`; klucz formularza 3 to wersja sprzed tej zmiany).
