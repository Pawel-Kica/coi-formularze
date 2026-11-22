# eFormatka: z papierowego formularza e-usługa COI

Wgrywasz pusty formularz urzędowy w dowolnym formacie (PDF, skan JPG/PNG, DOC, DOCX, ODS, XLS, XLSX), a REST API zwraca JSON e-usługi COI gotowy do importu. Urzędnik dostaje ten JSON jako szkic obok oryginału i poprawia go w edytorze: pod polem widzi cytat z dokumentu, z którego pole powstało, i „Źródło”, czyli wycinek oryginału z zaznaczonym miejscem. Model nie musi trafić wszystkiego za pierwszym razem: JSON to szkic, który urzędnik sprawdza i poprawia z oryginałem przed oczami.

{{ZRZUT_APLIKACJI}}

![Schemat: plik → odczyt stron → plan z cytatami → kod buduje i sprawdza JSON COI → urzędnik poprawia w przeglądarce](docs/img/prosty.png)

Projekt na HackNation 2026, zadanie COI „Cyfryzacja Formularzy”.

## Wyniki

Mierzymy to, co obchodzi urzędnika: ile poprawek musi zrobić w edytorze, zanim JSON pójdzie do importu.

| | |
|---|---|
| Poprawki urzędnika | średnio **{{POPRAWKI_SREDNIO}}** na formularz, który ma średnio {{POLA_SREDNIO}} pól; szczegóły w tabeli niżej |
| Pliki, z których powstał JSON | **{{PLIKI_OK}}** z 69 (9 formularzy z zadania we wszystkich formatach) |
| Problemy z regułami COI | **0**. API nie wypuszcza JSON-a, który łamie schemat COI albo „Reguły techniczne”, tylko odpowiada 422 |
| Czas i koszt | średnio **{{CZAS_FORMULARZ}}** i **{{KOSZT_FORMULARZ}}** na formularz (modele na Eden AI) |
| Kalibracja sędziów | {{KALIBRACJA}} |

**Jak liczymy poprawki.** Trzech sędziów AI dostaje strony oryginału i nasz JSON jako czytelny konspekt. Każdy osobno wypisuje poprawki, które urzędnik zrobiłby w edytorze: brakujące pole, zmyślone pole, zły typ, złe opcje, zły albo brakujący warunek, złe „wymagane”. Poprawka liczy się, gdy wypiszą ją co najmniej 2 z 3 sędziów. Samych sędziów sprawdziliśmy na 10 błędach, które celowo wstawiliśmy do poprawnych JSON-ów (wiersz „Kalibracja sędziów”). Przepis: `bench/JUDGE-POPRAWKI.md`.

### Każdy formularz z zadania

{{POPRAWKI_TABELA}}

### Porównanie podejść

Te same dokumenty i ci sami sędziowie, od najprostszego podejścia (cały plik prosto do jednego modelu, który ma oddać JSON COI) do naszego. Przy każdym podejściu podajemy model:

{{PORÓWNANIE}}

JSON-y do oddania są w `output/`: `output/api/` to surowy wynik API, a `output/` to ta sama e-usługa po naszych poprawkach. Liczba poprawek każdego formularza jest w `output/POPRAWKI.md`.

## Szybki start

Docker 23+ i klucz Eden AI:

```bash
cp .env.example .env          # wpisz EDENAI_API_KEY, to jedyny wymagany sekret
docker compose up -d --build
curl -X POST http://localhost:8000/api/forms/convert -F "file=@wniosek.pdf" -o wniosek.json
```

Bez Dockera: Python 3.14+, [uv](https://docs.astral.sh/uv/) i LibreOffice ([szczegóły](#natywnie-macos-linux)):

```bash
cp .env.example .env && uv sync
uv run uvicorn app.api:app --port 8000
```

Strona dla urzędnika: http://localhost:8000. OpenAPI: http://localhost:8000/docs. Stan: http://localhost:8000/health. Jeden formularz trwa od pół minuty do kilku minut. Gdy klient nie może tyle czekać, jest [zadanie w tle](#zadanie-w-tle).

## Jak z tego korzystać

1. **Wgraj plik** na http://localhost:8000: upuść go gdziekolwiek na stronie albo wybierz z dysku. Kilka plików naraz przetwarza się po kolei, a „Pobierz wszystkie” daje ZIP z ich JSON-ami.
2. **Porównaj.** Z lewej oryginał, z prawej e-usługa tak, jak zobaczy ją obywatel. Wszystkie kroki przeklikasz bez wypełniania pól.
3. **Sprawdź.** Pod każdym polem jest jego cytat z dokumentu i warunek, który je pokazuje. „Źródło” rozwija wycinek oryginału z ramką na tym cytacie, a klik w wycinek pokazuje całą stronę.
4. **Popraw.** Ołówek przy elemencie otwiera panel „Edycja” obok podglądu: etykieta, typ, opcje, „wymagane”, warunek „pokaż, gdy…”, dodawanie i usuwanie pól, sekcji, stron i oświadczeń, „Cofnij”. Każdą zmianę sprawdzają reguły COI: z problemem nie da się pobrać JSON-a, a usunięcie, które zepsułoby import, jest wyłączone i podaje powód.
5. **Pobierz JSON** gotowy do importu.

Do tego:
- **Słowniki** (bonus II). Zakładka „Słowniki” przy wyniku pokazuje propozycje nowych słowników dla pól formularza. „Dodaj” zapisuje słownik w katalogu na stronie `/slowniki` i podpina go pod pole.
- **Bez dokumentu.** Gotowy JSON e-usługi (np. z innego systemu) albo „+ Zacznij od pustego formularza” otwiera się od razu w edytorze, bez modelu.
- **Sam podgląd** dowolnego JSON-a COI: http://localhost:8000/preview.
- **Historia** plików jest na serwerze, więc zostaje po odświeżeniu strony. `HISTORY_ENABLED=0` ją wyłącza.

## Co z zadania jest zrobione

| Punkt zadania | Co mamy | Gdzie |
|---|---|---|
| 3.2.I REST API: plik → JSON COI | `POST /api/forms/convert`, każdy wynik przechodzi reguły COI przed odpowiedzią | [REST API](#rest-api) |
| 3.2.II Dokumentacja architektury | diagramy i opis kroków | [Architektura](#architektura) |
| 3.3.I Słowniki i dane inicjalne | strona „Dane” z rejestru MLPZ według Standardów COI, słowniki Kraje, TERYT i Numery kierunkowe; pole z rejestru PESEL tylko wtedy, gdy na pewno dotyczy samego wnioskodawcy | `app/builder/standard.py`, `registry.py` |
| 3.3.II Nowy słownik | propozycje z wartościami z dokumentu, katalog słowników z dodawaniem do pola | `POST /api/dictionaries/proposals`, `/slowniki` |
| 3.3.III Zależności | warunki „pokaż, gdy…” na polach i sekcjach, każdy z cytatem; sekcje powtarzalne. Schemat COI nie ma warunku na stronie, więc przejście zależne od odpowiedzi to warunki na sekcjach | `app/llm/planner.py`, `app/builder/` |
| 3.3.IV Frontend | strona `/`: upload, kilka plików naraz, historia, JSON | [Jak z tego korzystać](#jak-z-tego-korzystać) |
| 3.3.V Wizualizacja e-usługi | podgląd tak, jak widzi go obywatel, z edytorem; osobno `/preview` | `app/static/preview.html` |
| 3.3.VI Walidacje i UX | walidatory COI z etykiet (PESEL, NIP, REGON, kod pocztowy, e-mail, ePUAP, ADE, dowód osobisty), „wymagane”, przy telefonie numer kierunkowy i podpowiedź formatu; batch i ZIP | `app/builder/` |
| 5. Formaty, MIME z zawartości, nazwa pliku, limit rozmiaru | wszystko z listy zadania plus `.xls` i `.jpe` | [Walidacja i limity](#walidacja-i-limity) |
| 5. Tylko tymczasowe przetwarzanie | `convert` trzyma plik w pamięci; `HISTORY_ENABLED=0` wyłącza historię | [Przechowywanie plików](#przechowywanie-plików) |
| 5. Docker, konfiguracja, README | Alpine, wersje przypięte, użytkownik bez uprawnień, sekrety tylko przy starcie | [Budowanie obrazu Docker](#budowanie-obrazu-docker), [Konfiguracja](#konfiguracja) |
| 5. Python 3.14+, open source bez copyleft, bez modeli dostępnych tylko przez API | GLM-5.2 (MIT) i Qwen3.5 (Apache-2.0) mają otwarte wagi. Eden AI tylko je hostuje, a dostęp dali organizatorzy zadania. Własny serwer z tymi wagami ustawia się w konfiguracji, bez zmian w kodzie | [Licencje](#licencje) |

## Architektura

![Architektura: upload, odczyt stron, planer w 2 przebiegach, filtr cytatów, builder, reguły COI; modele na Eden AI, słowniki, strona dla urzędnika; pętla jakości](docs/img/architektura.png)

Model czyta dokument, a kod pilnuje formatu COI. Modele oddają tylko Markdown stron i krótki plan formularza. Wszystko po nich to deterministyczny kod z testami. Kroki skleja `app/pipeline.py`, REST jest w `app/api.py`.

Dlaczego tak:
- **Jedna ścieżka odczytu dla każdego formatu.** Każdy plik staje się obrazami stron, a Qwen3.5 przepisuje każdą stronę do Markdown. Skan, PDF i Word idą tą samą drogą, bez osobnego kodu na skany. Obrazy wygrały z odczytem tekstu w pomiarze, najmocniej na skanach ([historia](#jak-doszliśmy-do-tej-architektury)).
- **Każdy element planu niesie cytat z dokumentu.** Kod wyrzuca pole, opcję albo warunek, którego cytatu nie ma w odczytanym tekście, więc model nie może dopisać czegoś od siebie. Ten sam cytat urzędnik widzi pod polem, a „Źródło” pokazuje go w oryginale.
- **JSON COI pisze kod, nie model.** UUID, `techName`, siatkę, warunki JSONata, walidatory, słowniki i strony ze Standardów COI (Dane, Kontakt i zgody, strona końcowa) builder składa z gotowych klocków. Dlatego format jest zawsze poprawny, a reguły COI sprawdzamy jeszcze raz przed odpowiedzią.
- **Dwa przebiegi planera.** Najpierw struktura (strony, sekcje, pola, opcje, oświadczenia), potem warunki. W drugim przebiegu schemat odpowiedzi dopuszcza tylko istniejące pola i opcje.

![Kroki 1-3 w szczegółach: plik → obrazy stron → Markdown → plan w 2 przebiegach; Tesseract tylko do „Źródła”](docs/img/odczyt.png)

Kroki po kolei:
1. **Upload** (`app/upload.py`). Plik idzie do pamięci, a limit rozmiaru działa w trakcie odczytu. Potem kod sprawdza nazwę, rozszerzenie i sygnaturę bajtów. Przy `AUTH_ENABLED=1` żądanie potrzebuje tokenu JWT (`app/security.py`).
2. **Odczyt** (`app/llm/transcribe.py`). PDF renderuje PDFium, pliki biurowe najpierw zamienia na PDF LibreOffice, a wysoki skan z kilkoma stronami kod tnie na strony. Każda strona, zmniejszona do 1024 px, idzie osobno do Qwen3.5-397B-A17B, cztery naraz.
3. **Plan** (`app/llm/planner.py`, kontrakt `app/plan.py`). GLM-5.2 czyta sam Markdown, bez trybu myślenia, a odpowiedź wymusza schemat JSON. Przebieg 3a ustala strukturę, przebieg 3b warunki.
4. **Filtr cytatów i naprawy** (`app/quotes.py`, `app/repair.py`). Wypada element bez cytatu w Markdownie (dopasowanie rozmyte, `rapidfuzz`), pole, które kod i tak buduje na stronie „Dane”, i warunek bez oparcia w dokumencie (pole zostaje). Naprawy bez modelu: lista z opcją spoza dokumentu staje się polem tekstowym, podwójne pytanie i podwójne pole zostają raz, adres do korespondencji dostaje pytanie Tak/Nie, gdy dokument mówi „inny niż”.
5. **Builder** (`app/builder/`). Plan staje się pełną konfiguracją COI. Builder zwraca też mapę dowodów: przy każdym elemencie cytat z dokumentu albo informacja, że dodał go kod.
6. **Reguły** (`app/rules.py`). Schemat JSON COI i „Reguły techniczne”. Każdy problem kończy żądanie kodem 422.

Tesseract nie czyta treści formularza. Daje tylko ramki słów na skanach (`app/sources.py`, tryb `psm 11`), żeby „Źródło” i nagłówek „W dokumencie” trafiły w miejsce na stronie. PDF z warstwą tekstu i pliki biurowe mają te ramki bez OCR.

Diagramy to pliki HTML obok PNG w `docs/img/`. Liczby na nich są w `docs/img/liczby.json`, a `python3 docs/img/render.py` renderuje wszystkie od nowa (potrzebny Google Chrome).

## Budowanie obrazu Docker

```bash
docker build -t coi-formularze:0.1.0 .
```

Obraz buduje też `docker compose build`. Potrzebny jest Docker 23+, bo `Dockerfile` używa `RUN --mount=type=cache` (BuildKit).

- Oficjalny obraz `python:3.14.10-alpine3.24` w obu etapach budowy. `uv==0.13.9` instaluje zależności dokładnie z `uv.lock` (z hashami), a do obrazu końcowego trafia tylko środowisko i kod `app/`.
- LibreOffice Writer i Calc z fontami Liberation zamieniają pliki biurowe na obrazy stron. Lżejszy obraz bez nich (`--build-arg LIBREOFFICE=0`) przyjmie tylko PDF, JPG i PNG.
- Tesseract z danymi `pol`, tylko do ramek słów na skanach.
- Proces działa jako użytkownik bez uprawnień (UID 10001), wystawia tylko port 8000 i ma `HEALTHCHECK` na `/health`.
- Sekrety przychodzą tylko przy uruchomieniu, z `.env`. `.dockerignore` wyklucza `.env`, więc plik nie trafia do obrazu.
- Nigdzie `latest`. Pakiety systemowe pochodzą z zamrożonej gałęzi Alpine 3.24 bez numerów rewizji, bo Alpine usuwa stare rewizje i przypięty build przestałby się budować.

## Uruchomienie aplikacji

### Docker Compose

```bash
cp .env.example .env          # wpisz EDENAI_API_KEY; z AUTH_ENABLED=1 także JWT_SECRET i AUTH_PASSWORD
docker compose up -d --build  # strona http://localhost:8000, OpenAPI /docs, stan /health
```

`docker-compose.yml` (Compose 2.24+) dodaje system plików tylko do odczytu, `cap_drop: ALL` i `no-new-privileges`. Kontener zapisuje tylko do `/tmp` w pamięci (tmpfs, 1 GB): pliki tymczasowe LibreOffice i Tesseracta oraz historię strony, która znika przy restarcie. Modele są na Eden AI, więc kontener potrzebuje tylko klucza i internetu.

### Bez compose (`docker run`)

```bash
docker run --rm -p 8000:8000 --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges \
  --env-file .env coi-formularze:0.1.0
```

### Natywnie (macOS, Linux)

Potrzebujesz Pythona 3.14+, [uv](https://docs.astral.sh/uv/) i LibreOffice (`brew install --cask libreoffice` albo `apt install libreoffice-writer libreoffice-calc`). Tesseract z językiem polskim jest potrzebny tylko do „Źródła” na skanach (`brew install tesseract tesseract-lang` albo `apt install tesseract-ocr tesseract-ocr-pol`).

```bash
cp .env.example .env          # wpisz EDENAI_API_KEY
uv sync
uv run uvicorn app.api:app --port 8000
uv run pytest                 # testy bez sieci, żaden nie woła modelu
```

## Konfiguracja

Zmienne środowiskowe albo plik `.env` (`app/config.py`, wzór w `.env.example`). Trzeba podać tylko `EDENAI_API_KEY`, reszta ma wartości domyślne.

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `EDENAI_API_KEY` | brak | **Jedyne wymagane ustawienie.** Klucz Eden AI do każdego wywołania modelu. Tylko w `.env` albo w zmiennej środowiskowej, nigdy w repo ani w obrazie, nie trafia do logów |
| `MAX_FILE_SIZE_MB` | `10` | Maksymalny rozmiar pliku (MiB) |
| `AUTH_ENABLED` | `0` | `0` = API otwarte, strony bez logowania. `1` = każde wywołanie `/api/...` wymaga tokenu `Bearer` JWT, strony pokazują logowanie |
| `JWT_SECRET` | brak | Klucz podpisu tokenów, min. 32 znaki (`openssl rand -hex 32`). Bez niego API losuje klucz na czas życia procesu i ostrzega w logu |
| `JWT_ALGORITHM` | `HS256` | Algorytm podpisu (z `JWT_JWKS_URL` np. `RS256`) |
| `JWT_ISSUER`, `JWT_AUDIENCE` | `coi-formularze` | Wymagane `iss` i `aud` w tokenie |
| `JWT_TTL_MINUTES` | `60` | Ważność wydanego tokenu |
| `AUTH_USERNAME`, `AUTH_PASSWORD` | `admin`, brak | Login i hasło do `/api/auth/token`. Bez hasła logowanie lokalne jest wyłączone |
| `JWT_JWKS_URL` | brak | Zewnętrzny serwer tożsamości (np. Keycloak): API sprawdza tokeny jego kluczami publicznymi, logowanie lokalne jest wyłączone |
| `LLM_BASE_URL` | `https://api.edenai.run/v3` | API czatu zgodne z OpenAI. Własny serwer z tymi samymi wagami działa bez zmian w kodzie (jego klucz idzie w `EDENAI_API_KEY`) |
| `LLM_MODEL` | `deepinfra/zai-org/GLM-5.2` | Planer: czyta Markdown stron i pisze plan formularza |
| `TRANSCRIBE_MODEL` | `deepinfra/Qwen/Qwen3.5-397B-A17B` | Model wizyjny, który przepisuje każdą stronę do Markdown |
| `LLM_TIMEOUT_S` | `1200` | Limit czasu jednego wywołania planera (s) |
| `TRANSCRIBE_SIDE` | `1024` | Dłuższy bok obrazu strony wysyłanego do przepisania (px) |
| `TRANSCRIBE_WORKERS` | `4` | Ile stron przepisywanych naraz |
| `TRANSCRIBE_TIMEOUT_S` | `180` | Limit czasu jednej strony (s), potem jedna ponowna próba |
| `SOFFICE` | `soffice` | Program LibreOffice (na macOS kod sprawdza też ścieżkę aplikacji) |
| `HISTORY_ENABLED` | `1` | `0` = serwer nie zachowuje żadnego pliku ani wyniku po żądaniu, `/api/history` odpowiada 404. Katalog słowników zostaje, bo trzyma tylko nazwy i wartości |
| `HISTORY_DB` | `history.sqlite3`, w Dockerze `/tmp/history.sqlite3` | Plik SQLite z historią strony i katalogiem słowników |
| `LIBREOFFICE` | `1` | Tylko przy budowie obrazu (compose czyta go z `.env`): `0` = obraz bez LibreOffice |

## REST API

Przy `AUTH_ENABLED=1` każda trasa `/api/...` poza `/api/auth/*` wymaga nagłówka `Authorization: Bearer <token>`. Pełny schemat żądań i odpowiedzi jest w `/docs`.

| Trasa | Co robi |
|---|---|
| `POST /api/forms/convert` | Jeden plik w polu `file` (multipart) → czysty JSON COI. Z `?evidence=1` → `{"coi", "evidence"}`: przy każdym elemencie cytat z dokumentu albo informacja, że dodał go kod (tego używa strona) |
| `POST /api/forms/jobs` | To samo bez czekania. Te same kontrole pliku od razu, potem `202 {"id", "status_url", "result_url"}`. 429, gdy 20 zadań jest w toku |
| `GET /api/forms/jobs/{id}/result` | **404, dopóki formularz się liczy** (z `Retry-After`), potem JSON COI albo ten sam kod błędu co `convert`. Wynik czeka 1 h |
| `GET /api/forms/jobs/{id}` | `{"id", "status": "pending" \| "done" \| "error"}`, przy błędzie też `code` i `detail` |
| `POST /api/forms/check` | JSON COI → `{"problems": [{"id", "message", "detail"}]}` po polsku. Pusta lista = gotowe do importu. Te same reguły co przy konwersji |
| `POST /api/forms/text` | Tekst pliku biurowego albo warstwa tekstu PDF, bez modelu. Strona pokazuje go jako oryginał, gdy LibreOffice nie zrobi PDF-u |
| `GET /api/forms/blank` | `{"coi", "evidence"}` pustego formularza do zbudowania ręcznie: strony standardowe COI wokół jednej strony z jednym polem |
| `POST /api/dictionaries/proposals` | Bonus II: ten sam upload co `convert` → propozycje nowych słowników (nazwa, kod + nazwa, wartości, pole) i to, co kod odrzucił. JSON e-usługi się nie zmienia |
| `GET/POST /api/dictionaries`, `GET/PUT/DELETE /api/dictionaries/{code}`, `POST /api/dictionaries/{code}/items` | Katalog słowników: słowniki COI tylko do odczytu, nasze z wartościami `{kod, nazwa}` do zmiany |
| `GET/POST/DELETE /api/history`, `PUT/DELETE /api/history/{id}`, `GET /api/history/{id}/file`, `GET /api/history/{id}/pdf` | Historia strony: wpisy, oryginał pliku, plik biurowy jako PDF z LibreOffice. 404 przy `HISTORY_ENABLED=0` |
| `POST /api/history/{id}/sources`, `GET /api/history/{id}/page/{n}.png` | „Źródło”: miejsce każdego elementu w oryginale i strona oryginału jako PNG |
| `POST /api/auth/token`, `GET /api/auth/config` | Token JWT za login i hasło. Czy token jest potrzebny i czy serwer prowadzi historię |
| `GET /health` | `{"status", "llm": "ok" \| "no_api_key", "llm_model", "transcribe_model"}`. Sprawdza tylko, czy jest klucz, nie woła Edenu |

Strony: `/` (urzędnik), `/preview` (sam podgląd), `/slowniki` (katalog słowników), `/docs` (OpenAPI).

```bash
# AUTH_ENABLED=0 (domyślnie)
curl -X POST http://localhost:8000/api/forms/convert -F "file=@wniosek.pdf" -o wniosek.json

# AUTH_ENABLED=1: najpierw token
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/token -H 'Content-Type: application/json' \
  -d '{"username": "admin", "password": "haslo"}' | jq -r .access_token)
curl -X POST http://localhost:8000/api/forms/convert -H "Authorization: Bearer $TOKEN" -F "file=@wniosek.pdf" -o wniosek.json
```

### Zadanie w tle

Wyślij plik, potem pytaj o adres wyniku, aż przestanie odpowiadać 404:

```bash
BASE=http://localhost:8000
RESULT=$BASE$(curl -s -X POST $BASE/api/forms/jobs -F "file=@wniosek.pdf" | jq -r .result_url)
for i in $(seq 240); do   # do 20 minut
  CODE=$(curl -s -o wniosek.json -w '%{http_code}' "$RESULT"); [ "$CODE" != 404 ] && break; sleep 5
done; echo "$CODE"       # 200 = JSON COI, inny kod = błąd opisany w wniosek.json
```

Zadanie, którego nie ma (nieznane albo wygasłe), też daje 404 na adresie wyniku. Odróżnisz je przez `status_url`: tam 404 znaczy właśnie to, a liczące się zadanie odpowiada `pending`.

### Błędy

Każdy błąd to JSON z polem `detail`, zwykle `{"detail": "opis po polsku"}`.

| Kod | Kiedy |
|---|---|
| 400 | zła nazwa pliku, pusty plik, brak pliku albo więcej niż jeden plik; brak `username` lub `password` przy logowaniu |
| 401 | brak, zły lub wygasły token; zły login lub hasło (tylko przy `AUTH_ENABLED=1`) |
| 404 | wynik zadania jeszcze się liczy albo zadanie jest nieznane; `/api/history` przy `HISTORY_ENABLED=0` |
| 413 | plik większy niż `MAX_FILE_SIZE_MB` |
| 415 | nieobsługiwane rozszerzenie albo zawartość niezgodna z rozszerzeniem |
| 422 | nie udało się odczytać formularza albo wynik łamałby reguły COI |
| 429 | 20 zadań w tle już w toku |
| 500 | nieoczekiwany błąd serwera (szczegóły tylko w logu) |
| 502 / 503 / 504 | model zwrócił błąd / jest niedostępny albo brak `EDENAI_API_KEY` / nie odpowiedział na czas. 503 także z `/api/auth/token`, gdy logowanie lokalne jest wyłączone (brak `AUTH_PASSWORD` albo tokeny wydaje `JWT_JWKS_URL`) |

## Walidacja i limity

- Jeden plik na żądanie, jako `multipart/form-data` w polu `file`. Żądanie bez pliku albo z więcej niż jednym plikiem to 400.
- Rozszerzenia, bez względu na wielkość liter: `.pdf .doc .docx .ods .xlsx .xls .jpg .jpeg .jpe .png`. Inne to 415. `.xls` i `.jpe` to świadomy dodatek do listy z zadania, bo spotykamy je w BIP-ach gmin.
- Zadanie wymienia DOC jako „.doc .docx”, więc oba rozszerzenia przyjmują oba formaty Worda (gminy publikują DOCX z końcówką `.doc`). Zawartość nadal musi być prawdziwym dokumentem Worda.
- Nazwa pliku: najwyżej 250 znaków, bez `; : > < / \ | ' * % $`.
- Typ MIME wynika z zawartości i musi pasować do rozszerzenia; nagłówek `Content-Type` od klienta nie decyduje. PDF: `%PDF`. DOC i XLS: plik OLE2 ze strumieniem `WordDocument` albo `Workbook`. DOCX i XLSX: ZIP z `[Content_Types].xml` i katalogiem `word/` albo `xl/`. ODS: `mimetype` = `application/vnd.oasis.opendocument.spreadsheet`. JPEG: `FF D8 FF`. PNG: sygnatura PNG.
- Rozmiar jest liczony w trakcie odczytu: żądanie kończy się zaraz po przekroczeniu limitu, niezależnie od `Content-Length`.

### Przechowywanie plików

- `POST /api/forms/convert`: plik jest tylko w pamięci i po odpowiedzi nie zostaje ani on, ani wynik. Na dysk na czas żądania trafiają tylko pliki tymczasowe LibreOffice i Tesseracta. Obrazy stron idą do modelu na Eden AI, nie na dysk. W pamięci procesu zostaje odczyt stron (Markdown) 16 ostatnich plików, żeby propozycje słowników nie płaciły za ten sam odczyt drugi raz; znika przy restarcie.
- `POST /api/forms/jobs`: plik jest w pamięci procesu do końca zadania, także po błędzie. Zostaje sam wynik JSON, przez 1 h. Restart serwera zapomina wszystkie zadania.
- Historia strony `/` to funkcja interfejsu, nie API: zachowuje oryginał i wynik, żeby urzędnik mógł wrócić do pliku. W Dockerze leży w `/tmp` w pamięci kontenera. `HISTORY_ENABLED=0` wyłącza ją całkiem.

## Jak doszliśmy do tej architektury

Każdy krok rozstrzygnął pomiar na formularzach COI z zadania:
1. **Lokalny model był za słaby.** Zaczęliśmy od qwen3 30B na laptopie. Działał od pliku do JSON-a, ale mocniejszy model na tym samym tekście i promptach pokazał, że wąskim gardłem jest model, nie odczyt ani kod.
2. **Obrazy stron zamiast tekstu.** Na Eden AI porównaliśmy tekst z naszej ekstrakcji i Tesseracta z obrazami stron przepisanymi przez Qwen3.5. Obrazy wygrały, najmocniej na skanach, i zostały jedyną ścieżką.
3. **GLM-5.2 jako planer.** Na tym samym Markdownie porównaliśmy cztery modele. GLM-5.2 dał najlepszy plan, 10 razy szybciej niż Qwen3.5 w tej roli i za podobne pieniądze.
4. **Potem pętla jakości.** Każda zmiana promptu albo kodu po modelu wchodziła tylko wtedy, gdy nie przybywało poprawek urzędnika i problemów z regułami.

![Trzy pomiary: lokalny model wobec mocniejszego, tekst wobec obrazów stron, cztery planery](docs/img/historia.png)

Tabele, czasy, koszty i odrzucone pomysły są w [docs/historia.md](docs/historia.md). Pomiary robią `bench/run_all.py` (woła modele, kosztuje) i `bench/score.py` (bez modelu).

## Znane ograniczenia

- Najsłabsze są warunki na długich formularzach (np. 16-stronicowy wniosek „3 w 1”). Model znajduje część zależności, resztę urzędnik ustawia w panelu „Edycja” wyborem „pokaż, gdy…”.
- Ten sam plik wysłany dwa razy może dać trochę inny szkic, bo modele nie są powtarzalne co do bitu. Dlatego każdą zmianę mierzyliśmy na kilku dokumentach.
- Modele działają na Eden AI, do którego dostęp dali organizatorzy zadania. Wagi są otwarte, więc te same modele można postawić na własnym serwerze z API zgodnym z OpenAI: adres w `LLM_BASE_URL`, nazwy w `LLM_MODEL` i `TRANSCRIBE_MODEL`, klucz tego serwera w `EDENAI_API_KEY`.
- Formularz dłuższy niż 30 stron jest czytany do 30. strony.
- Zadania w tle i odczyty stron żyją w pamięci jednego procesu, więc usługa działa jako jedna instancja. Kilka instancji za load balancerem wymagałoby wspólnej kolejki zadań.

## Licencje

Kod i zależności to wyłącznie open source bez copyleft.
- Zależności Pythona: MIT, BSD, Apache-2.0, PSF i MIT-CMU (Pillow). `certifi` (zależność `httpx`) ma MPL-2.0, która obejmuje tylko jego własne pliki i nie wymaga publikacji naszego kodu.
- Tesseract: Apache-2.0. LibreOffice: MPL-2.0, uruchamiany jako osobny program, bez linkowania z naszym kodem.
- Modele: GLM-5.2 (planer) na licencji MIT, Qwen3.5-397B-A17B (odczyt stron) na Apache-2.0. Oba mają otwarte wagi.
