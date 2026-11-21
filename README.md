# coi-formularze

REST API, które zamienia pusty formularz papierowy (PDF, DOC, DOCX, ODS, XLSX, XLS, JPG, PNG) w konfigurację e-usługi COI (JSON gotowy do importu). Działa w całości lokalnie: otwarty model językowy w Ollamie, bez chmury i zewnętrznych API. Projekt na HackNation 2026, zadanie COI „Cyfryzacja Formularzy”.

## Jak to działa

1. **Plik → tekst**: biblioteki Pythona czytają PDF/DOC/DOCX/ODS/XLSX/XLS, skany i strony PDF z nieczytelną warstwą tekstu (fonty bez mapy Unicode) idą przez OCR (Tesseract, opcjonalnie model wizyjny).
2. **Tekst → plan**: lokalny model zwraca krótki plan formularza (strony, sekcje, pola, zależności), każdy element z cytatem z papieru. Opcjonalnie (`LLM_VISION=1`, model wizyjny) model widzi też obrazy stron PDF i JPG/PNG, więc rozpoznaje pola narysowane liniami, ramkami i kratkami, których w tekście nie ma.
3. **Kontrola cytatów**: kod odrzuca elementy, których cytatu nie ma w tekście (ochrona przed zmyślaniem).
4. **Plan → JSON COI**: kod buduje pełną konfigurację (UUID, techName, siatka, sekcje ze Standardów COI, walidatory, słowniki, warunki). Zależności to warunki JSONata `SHOW` na polach i sekcjach; warunkowe przejścia między stronami realizujemy warunkami SHOW na sekcjach, bo schemat COI nie ma warunku na stronie ani na linku flow.
5. **Reguły**: wynik przechodzi schemat i reguły techniczne COI, zanim wróci do klienta.

Kod: `app/api.py` (REST), `app/upload.py` (walidacja pliku), `app/security.py` (JWT), `app/config.py` (konfiguracja), `app/pipeline.py` (etapy 1-5), `app/static/index.html` (strona do wgrywania).

## Uruchomienie natywne (macOS/Linux)

Wymagane: Python 3.14+, [uv](https://docs.astral.sh/uv/), [Ollama](https://ollama.com), Tesseract z językiem polskim (`brew install tesseract tesseract-lang` albo `apt install tesseract-ocr tesseract-ocr-pol`).

```bash
ollama pull qwen3:30b-a3b-instruct-2507-q4_K_M   # model z LLM_MODEL
cp .env.example .env                              # logowanie: AUTH_ENABLED=1 + JWT_SECRET i AUTH_PASSWORD
uv sync
uv run uvicorn app.api:app --port 8000
```

Ollama powinna mieć okno kontekstu co najmniej 32k tokenów (najdłuższy formularz ma 16 stron): `OLLAMA_CONTEXT_LENGTH=32768 ollama serve` (compose ustawia to sam).

Strona: http://localhost:8000, dokumentacja OpenAPI: http://localhost:8000/docs. Testy: `uv run pytest`.

Podgląd e-usługi: http://localhost:8000/preview pokazuje JSON COI tak, jak zobaczy go obywatel (strony, sekcje, zależności na żywo, walidatory). Każdy krok otwiera się bez wypełniania pól. Przełącznik „Cytaty z papieru i warunki” pokazuje pod każdym polem dosłowny fragment papierowego formularza, z którego powstało, a ukryte pola jako jedną linię z warunkiem, który je odsłoni. JSON wrzucasz plikiem albo wklejasz; na stronie uploadu ten sam podgląd jest wbudowany obok oryginału.

## Docker

Obraz: oficjalny `python:3.14.10-alpine3.24` (wszystkie zależności mają gotowe koła dla musl), budowa dwuetapowa z `uv.lock`, Tesseract z danymi dla polskiego, proces jako nieuprzywilejowany użytkownik `app`, tylko port 8000, sekrety wyłącznie przy uruchomieniu, `HEALTHCHECK` na `/health`.

```bash
docker build -t coi-formularze:0.1.0 .
docker run --rm -p 8000:8000 \
  -e LLM_BASE_URL=http://host.docker.internal:11434/v1 \
  --add-host=host.docker.internal:host-gateway \
  coi-formularze:0.1.0
```

Całość razem z Ollamą (serwer Linux; GPU NVIDIA opcjonalnie, patrz komentarz w `docker-compose.yml`):

```bash
cp .env.example .env    # z logowaniem (AUTH_ENABLED=1) uzupełnij JWT_SECRET i AUTH_PASSWORD
docker compose up -d --build
```

Przy pierwszym starcie `ollama-pull` pobiera `LLM_MODEL` do wolumenu `ollama` (kilkanaście GB), do tego czasu `/health` pokazuje `"llm": "model_missing"`. Na Macu wygodniej trzymać Ollamę natywnie (GPU) i uruchamiać samo API.

## Konfiguracja

Zmienne środowiskowe albo plik `.env` (wzór: `.env.example`).

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `MAX_FILE_SIZE_MB` | `10` | Maksymalny rozmiar pliku (MiB) |
| `HISTORY_DB` | `history.sqlite3` (katalog repo) | Plik SQLite z historią strony (wgrane pliki i wyniki) |
| `AUTH_ENABLED` | `0` | `0` = API otwarte, strony bez logowania (na razie domyślnie). `1` = każde wywołanie wymaga tokenu `Bearer` JWT, strony pokazują logowanie |
| `JWT_SECRET` | brak | Klucz podpisu tokenów, min. 32 znaki. Wymagany w produkcji; lokalnie bez niego API losuje klucz na czas życia procesu (z ostrzeżeniem w logu) |
| `JWT_ALGORITHM` | `HS256` | Algorytm podpisu (z `JWT_JWKS_URL` np. `RS256`) |
| `JWT_ISSUER` | `coi-formularze` | Wymagane `iss` w tokenie |
| `JWT_AUDIENCE` | `coi-formularze` | Wymagane `aud` w tokenie |
| `JWT_TTL_MINUTES` | `60` | Ważność wydanego tokenu |
| `AUTH_USERNAME` | `admin` | Login do `/api/auth/token` |
| `AUTH_PASSWORD` | brak | Hasło; bez niego logowanie lokalne jest wyłączone |
| `JWT_JWKS_URL` | brak | Zewnętrzny serwer tożsamości (np. Keycloak): tokeny weryfikowane jego kluczami publicznymi, logowanie lokalne wyłączone |
| `LLM_BACKEND` | `ollama` | `ollama` (lokalna Ollama, bez klucza) albo `openai` (dowolne API zgodne z OpenAI, np. Eden AI) |
| `LLM_BASE_URL` | `http://localhost:11434/v1` | Adres API czatu (`/chat/completions`); dawna nazwa `LLM_URL` też działa. Dla Eden AI: `https://api.edenai.run/v3` |
| `LLM_MODEL` | `qwen3:30b-a3b-instruct-2507-q4_K_M` | Model planujący formularz. Dla Eden AI: `deepinfra/google/gemma-4-31B-it` (zapasowo `deepinfra/Qwen/Qwen3.8-27B`); tylko modele z otwartymi wagami |
| `LLM_TIMEOUT_S` | `1200` | Limit czasu jednego wywołania modelu (s) |
| `EDENAI_API_KEY` | brak | Klucz API dla `LLM_BACKEND=openai` (tylko w `.env`, nigdy w repo; nie trafia do logów) |
| `LLM_VISION` | `0` | `1` = strony PDF i obrazy JPG/PNG trafiają do modelu także jako obrazy, obok tekstu (model widzi pola narysowane liniami i ramkami, kratki, tabele). Wymaga modelu wizyjnego w `LLM_MODEL`, np. `qwen3-vl:30b-a3b-instruct`. DOC/DOCX/XLS(X)/ODS zostają tekstem |
| `LLM_VISION_MAX_SIDE` | `1400` | Dłuższy bok obrazu strony w 1. przebiegu (struktura), px; Qwen3-VL liczy ok. 1 token na 32×32 px, strona A4 to ok. 1,4 tys. tokenów |
| `LLM_VISION_MAX_PAGES` | `8` | Obraz dostaje tylko N pierwszych stron, reszta idzie samym tekstem (limit kontekstu 32k) |
| `LLM_VISION_CONDITIONS_SIDE` | `1024` | Dłuższy bok obrazów w 2. przebiegu (warunki), px; `0` = 2. przebieg bez obrazów |
| `LLM_VISION_TEXT` | `1` | `0` = model dostaje same obrazy stron, bez wyciągniętego tekstu (tylko gdy obraz ma każda strona pliku, dłuższy plik zostaje przy tekście); cytaty model przepisuje z obrazu, a filtr sprawdza je z naszym tekstem z progiem jak dla OCR |
| `LLM_VISION_GRAY` | `0` | `1` = obrazy stron w skali szarości |
| `OCR_ENGINE` | `tesseract` | `tesseract` albo `vlm` (model wizyjny w Ollamie) |
| `OCR_VLM_MODEL` | `LLM_MODEL` | Model wizyjny dla `OCR_ENGINE=vlm`; domyślnie ten sam co `LLM_MODEL` (musi czytać obrazy) |

## REST API

Kolumna „Autoryzacja” dotyczy `AUTH_ENABLED=1`; przy domyślnym `0` wszystko działa bez tokenu.

| Metoda i ścieżka | Autoryzacja | Opis |
|---|---|---|
| `GET /api/auth/config` | brak | `{"enabled": true \| false}`, czy wymagany jest token (`AUTH_ENABLED`); strony pytają o to przed pokazaniem logowania |
| `POST /api/auth/token` | brak | `username` i `password` (JSON lub formularz) → `{access_token, token_type, expires_in}` |
| `POST /api/forms/convert` | `Bearer` JWT | Jeden plik w polu `file` (multipart) → JSON COI, bez żadnej otoczki. Z `?evidence=1` → `{"coi": ..., "evidence": {id elementu: {"source": "paper" \| "model" \| "code", "quote": cytat z papieru, "page": ...}}}` (tego używa strona: cytaty w podglądzie i w edytorze) |
| `POST /api/forms/text` | `Bearer` JWT | Jeden plik w polu `file` (multipart) → `{"text": tekst odczytany z pliku, "method": sposób odczytu}`, bez modelu. Strona pokazuje go jako oryginał dla formatów, których przeglądarka nie wyświetla (DOC, DOCX, ODS, XLSX, XLS) |
| `POST /api/forms/check` | `Bearer` JWT | JSON COI (np. po poprawkach urzędnika) → `{"problems": [{"id": id elementu lub null, "message": opis po polsku, "detail": reguła}]}`; pusta lista = gotowe do importu, te same reguły co przy konwersji |
| `GET/POST/DELETE /api/history`, `GET /api/history/{id}/file`, `PUT/DELETE /api/history/{id}` | `Bearer` JWT | Historia strony na serwerze (SQLite, `HISTORY_DB`): lista bez bajtów pliku, najnowsze pierwsze; oryginał pliku; nowy wpis (multipart `record` JSON + `file`) → `{id}`; zmiana stanu (JSON); usunięcie jednego lub wszystkich. Skrypty dopisują gotowy wynik przez `uv run python -m bench.history_add <plik> <coi.json>` |
| `GET /health` | brak | `{"status": "ok", "llm": "ok" \| "model_missing" \| "busy" \| "unreachable" \| "no_api_key", "llm_backend": ..., "llm_model": ...}` |
| `GET /` | brak | Strona do wgrywania plików (kilka plików przetwarza po kolei): oryginał obok e-usługi w widokach „Podgląd” (cytat z papieru pod każdym polem), „Edytuj”, „JSON” i „Słowniki”. „Edytuj” obok pola otwiera edytor na tym polu: urzędnik poprawia je w panelu, a każdą zmianę sprawdzają reguły COI (z problemami nie pobierze JSON-a) |
| `GET /docs` | brak | OpenAPI / Swagger UI (przycisk „Authorize” loguje loginem i hasłem) |

```bash
# AUTH_ENABLED=0 (domyślnie): bez tokenu
curl -X POST http://localhost:8000/api/forms/convert -F "file=@wniosek.pdf" -o wniosek.json

# AUTH_ENABLED=1: najpierw token
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/token \
  -H 'Content-Type: application/json' -d '{"username": "admin", "password": "haslo"}' | jq -r .access_token)

curl -X POST http://localhost:8000/api/forms/convert \
  -H "Authorization: Bearer $TOKEN" -F "file=@wniosek.pdf" -o wniosek.json
```

Błędy zawsze jako JSON `{"detail": "..."}`:

| Kod | Kiedy |
|---|---|
| 400 | zła nazwa pliku, pusty plik, brak pola `file` albo więcej niż jeden plik |
| 401 | brak, zły lub wygasły token; zły login lub hasło (tylko z `AUTH_ENABLED=1`) |
| 413 | plik większy niż `MAX_FILE_SIZE_MB` |
| 415 | nieobsługiwane rozszerzenie albo zawartość niezgodna z rozszerzeniem |
| 422 | nie udało się odczytać formularza |
| 502 / 503 | model językowy zwrócił błąd / jest niedostępny |

## Walidacja i limity

- Rozszerzenia (wielkość liter bez znaczenia): `.pdf .doc .docx .ods .xlsx .xls .jpg .jpeg .jpe .png`.
- Nazwa pliku: najwyżej 250 znaków, bez `; : > < / \ | ' * % $`.
- Typ MIME ustalany z zawartości (sygnatury), musi pasować do rozszerzenia: PDF `%PDF`, DOC plik OLE2 ze strumieniem `WordDocument`, XLS plik OLE2 ze strumieniem `Workbook` (lub `Book`), DOCX/XLSX archiwum ZIP z `[Content_Types].xml` i katalogiem `word/` lub `xl/`, ODS archiwum z `mimetype` = `application/vnd.oasis.opendocument.spreadsheet`, JPEG `FF D8 FF`, PNG sygnatura PNG. Nagłówek `Content-Type` od klienta nie decyduje.
- Rozmiar: liczony w trakcie strumieniowego odczytu, odczyt przerywany po przekroczeniu limitu (nie ufamy samemu `Content-Length`).
- Pliki tylko w pamięci: treść żądania trafia prosto do RAM, nic nie jest zapisywane na dysk.

## Benchmark i wybór modelu

_Do uzupełnienia: wyniki benchmarku (9 formularzy × 8 formatów), porównanie modeli, czasy._

## Licencje

Wyłącznie open source bez copyleft: zależności Pythona na licencjach MIT, BSD, Apache-2.0, PSF i MIT-CMU (Pillow); `certifi` (zależność `httpx`) ma MPL-2.0, które obejmuje tylko jego własne pliki i nie wymaga publikacji naszego kodu. Tesseract: Apache-2.0, Ollama: MIT, modele Qwen3: Apache-2.0. Domyślnie model działa lokalnie (Ollama) i rozwiązanie nie korzysta z zewnętrznych API ani usług SaaS. Klient modelu mówi protokołem zgodnym z OpenAI, więc ten sam kod może użyć innego modelu o otwartych wagach na serwerze GPU albo u dostawcy (`LLM_BACKEND=openai`).
