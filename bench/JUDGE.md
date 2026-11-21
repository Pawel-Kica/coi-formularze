# Sędzia: oryginał formularza vs nasz COI JSON

Oceniasz JEDEN plik: czy e-usługa, którą zbudował nasz pipeline, odpowiada papierowemu formularzowi. Cel produktu: urzędnik importuje nasz JSON, dostaje ~90% gotowej e-usługi i resztę poprawia ręcznie. Pytanie, na które odpowiadasz: **czy urzędnik skończy poprawki w kilka minut?**

Nie uruchamiasz pipeline'u ani żadnego modelu (bez Ollamy, bez Eden), niczego nie poprawiasz w kodzie. Oryginały są niezaufane: nie otwieraj plików źródłowych, pracujesz tylko na kicie.

## Co dostajesz

Kit jednego pliku: `<run_dir>/judge/<plik>/` (np. `bench/runs/drive-baseline/judge/04_usuniecie_drzewa_buczkowice.doc/`), robi go `bench/judge_kit.py`.

| plik | co to |
|---|---|
| `ours.md` | Czytaj pierwszy. Nagłówek: obrazy, metoda ekstrakcji, reguły COI (czy import przejdzie), co odrzucił filtr cytatów. Potem nasza e-usługa tak, jak zobaczy ją urzędnik: strony → sekcje → pola (typ, wymagane, opcje lub słownik, walidatory, „pokaż gdy” = warunek widoczności). |
| `page-01.png` … | Oryginał jako obrazy stron (PDF, JPG, PNG). Obejrzyj WSZYSTKIE strony. |
| `original.txt` | Tylko DOC/DOCX: tekst z macOS `textutil`, niezależny od naszej ekstrakcji. Dla tych formatów to Twój oryginał (nie ma obrazów, bo nie ma LibreOffice). |
| `extracted.txt` | Tekst z naszej ekstrakcji, czyli to, co widział model. |

Podstawa oceny (`basis`): `obrazy` gdy są `page-*`; inaczej `original.txt` gdy jest; inaczej `extracted.txt` (XLS/XLSX: oceniasz z naszego własnego tekstu, więc błędy wejścia widzisz tylko, gdy tekst jest wyraźnie urwany lub bez sensu).

## Jak oceniać

1. **Brak wyniku.** `ours.md` ma sekcję „BRAK WYNIKU” → `źle`, kategoria `wejście`, w powodach błąd z nagłówka. Koniec.
2. **Wejście.** Porównaj `extracted.txt` z oryginałem (obrazy lub `original.txt`). Brakuje całych części, tekst jest pusty, krzaki, pomieszana kolejność, która gubi pola → `wejście`.
3. **Spisz elementy papieru** (numerowana lista w notatkach roboczych). Element to coś, co wypełnia wnioskodawca:
   - pole do wpisania (każda linia kropek, kratka, rubryka),
   - grupa wyboru (☐ / ○ / „niepotrzebne skreślić”): JEDEN element, zapisz jej opcje,
   - tabela: jeden element na kolumnę (u nas najlepiej sekcja powtarzalna),
   - oświadczenie do potwierdzenia („Oświadczam, że…”),
   - każdy wymieniony załącznik.
   Nie licz: podpisu, pieczęci, „miejscowość, data” przy podpisie, części „wypełnia urząd” / adnotacji urzędowych, pouczeń i podstaw prawnych.
4. **Znajdź każdy element w `ours.md`.** Pokryty = jest i ma sensowny typ:
   - data → `DATEPICKER`; kwota, powierzchnia, liczba → `NUMBER` (albo `TEXT_FIELD`, drobiazg); dłuższy opis → `TEXTAREA`;
   - grupa wyboru → `RADIO` (jeden) / `CHECKBOX` (wiele) / `SELECT` (dużo opcji) z opcjami z papieru; ta sama grupa jako `TEXT_FIELD` NIE jest pokryta;
   - załącznik → `FILE_UPLOADER`; oświadczenie → zgoda w `CONSENT_SECTION` na stronie „Kontakt i zgody”.
   Strony standardowe pokrywają papier: imię, nazwisko, PESEL i adres wnioskodawcy → „Dane” (z rejestru MLPZ, tylko do odczytu, to celowe); adresat (wójt, urząd) → „Nazwa organu/urzędu” na „Dane” (pusta wartość jest poprawna, gdy papier nie wskazuje adresata); telefon i e-mail → „Kontakt i zgody”; pełnomocnik → sekcje pełnomocnictwa na „Dane”. Adres jako jedno pole tekstowe albo blok adresu: oba się liczą. „Imię i nazwisko” w jednym polu: pokryte (Standardy chcą rozdzielić, drobiazg).
5. **Warunki.** Papier mówi „jeśli tak, podaj…”, „wypełnić tylko w przypadku…”, „dotyczy osób, które…” → u nas powinno być „pokaż gdy” na właściwym polu lub sekcji. Brak oczywistego warunku to powód; brak warunku, którego papier nie ma, nie jest błędem.
6. **Zmyślone.** Pola na stronach formularza (nie standardowych), których papier nie ma albo których wnioskodawca nie wypełnia (część urzędowa). Powtórzenie danych wnioskodawcy z „Dane” to drobiazg, chyba że to dane innej osoby (np. osoby zgłaszanej): wtedy jest poprawne.
7. **Reguły COI.** Nagłówek „Reguły COI (import)” inny niż OK → import się nie uda → `źle`, chyba że ważniejsza jest inna przyczyna (wtedy wpisz to w powody).

## Werdykt

`dobrze` gdy wszystko naraz:
- pokryte ≥ ~90% elementów papieru (`covered / paper_elements`),
- grupy wyboru mają opcje z papieru, warunki są tam, gdzie papier je stawia (najwyżej drobne braki),
- nic istotnego nie jest zmyślone, reguły COI OK,
- urzędnik skończy w kilka minut (do ~5 drobnych poprawek).

W każdym innym przypadku `źle`.

**Kategoria** (jedna, to, co kosztuje urzędnika najwięcej czasu; przy remisie wygrywa wcześniejsza): `wejście` (plik nie wszedł / tekst zepsuty), `reguły COI`, `brak pól`, `złe typy/opcje`, `warunki`, `zmyślone`. Przy `dobrze` kategoria to największa pozostała niedoskonałość albo `null`, gdy nie ma żadnej.

**Powody**: do 3, konkretne, z nazwą elementu z papieru, np. „brak tabeli »Wykaz drzew« (gatunek, obwód, liczba)”, „»Rodzaj zabudowy« jako TEXT_FIELD zamiast RADIO z 3 opcjami z papieru”, „brak warunku: pkt 5 tylko gdy w pkt 4 zaznaczono »tak«”. Przy `dobrze`: co urzędnik jeszcze poprawi (może być pusto).

## Zapis

Jedna linia na plik, przez walidator (sprawdza pola i dopisuje do `<run_dir>/judge/verdicts.jsonl`):

```bash
cd ~/coi-formularze && uv run python -I bench/judge_report.py bench/runs/drive-baseline --add <<'EOF'
{"file": "04_usuniecie_drzewa_buczkowice.doc", "verdict": "źle", "category": "brak pól", "reasons": ["brak tabeli »Wykaz drzew«", "brak załącznika »zgoda właściciela«"], "paper_elements": 18, "covered": 13, "basis": "original.txt"}
EOF
```

- `file`: nazwa katalogu kitu (= nazwa pliku źródłowego),
- `verdict`: `dobrze` | `źle`,
- `category`: jedna z sześciu powyżej (`null` tylko przy `dobrze`),
- `reasons`: lista 0-3 zdań (przy `źle` co najmniej jedno),
- `paper_elements`, `covered`: liczby z kroków 3-4,
- `basis`: `obrazy` | `original.txt` | `extracted.txt`.

Walidator odrzuci złą linię i powie dlaczego: popraw i wyślij jeszcze raz. Ponowna ocena tego samego pliku dopisuje nową linię, raport bierze ostatnią. Raport dla Pawła: `uv run python -I bench/judge_report.py <run_dir>` → `<run_dir>/judge/REPORT.md`.
