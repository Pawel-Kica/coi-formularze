# coi-formularze

REST API, które zamienia pusty formularz papierowy (PDF, DOC, DOCX, ODS, XLSX, JPG, PNG) w konfigurację e-usługi COI (JSON gotowy do importu). Działa w całości lokalnie: otwarty model językowy w Ollamie, bez chmury i zewnętrznych API. Projekt na HackNation 2026, zadanie COI „Cyfryzacja Formularzy”.

## Jak to działa

1. **Plik → tekst**: biblioteki Pythona czytają PDF/DOC/DOCX/ODS/XLSX, skany idą przez OCR (Tesseract, opcjonalnie model wizyjny).
2. **Tekst → plan**: lokalny model zwraca krótki plan formularza (strony, sekcje, pola, zależności), każdy element z cytatem z papieru.
3. **Kontrola cytatów**: kod odrzuca elementy, których cytatu nie ma w tekście (ochrona przed zmyślaniem).
4. **Plan → JSON COI**: kod buduje pełną konfigurację (UUID, techName, siatka, sekcje ze Standardów COI, walidatory, słowniki, warunki). Zależności to warunki JSONata `SHOW` na polach i sekcjach; warunkowe przejścia między stronami realizujemy warunkami SHOW na sekcjach, bo schemat COI nie ma warunku na stronie ani na linku flow.
5. **Reguły**: wynik przechodzi schemat i reguły techniczne COI, zanim wróci do klienta.

Kod: `app/api.py` (REST), `app/upload.py` (walidacja pliku), `app/security.py` (JWT), `app/config.py` (konfiguracja), `app/pipeline.py` (etapy 1-5), `app/static/index.html` (strona do wgrywania).

## Uruchomienie natywne (macOS/Linux)

Wymagane: Python 3.14+, [uv](https://docs.astral.sh/uv/), [Ollama](https://ollama.com), Tesseract z językiem polskim (`brew install tesseract tesseract-lang` albo `apt install tesseract-ocr tesseract-ocr-pol`).

```bash
ollama pull qwen3:30b-a3b-instruct-2507-q4_K_M   # model z LLM_MODEL
cp .env.example .env                              # uzupełnij JWT_SECRET i AUTH_PASSWORD
uv sync
uv run uvicorn app.api:app --port 8000
```

Ollama powinna mieć okno kontekstu co najmniej 32k tokenów (najdłuższy formularz ma 16 stron): `OLLAMA_CONTEXT_LENGTH=32768 ollama serve` (compose ustawia to sam).

Strona: http://localhost:8000, dokumentacja OpenAPI: http://localhost:8000/docs. Testy: `uv run pytest`.

Podgląd e-usługi: http://localhost:8000/preview pokazuje JSON COI tak, jak zobaczy go obywatel (strony, sekcje, zależności na żywo, walidatory, przełącznik „pokaż techniczne”). JSON wrzucasz plikiem, wklejasz albo otwierasz przyciskiem „Podgląd” na stronie uploadu.

## Docker

Obraz: oficjalny `python:3.14.10-alpine3.24` (wszystkie zależności mają gotowe koła dla musl), budowa dwuetapowa z `uv.lock`, Tesseract z danymi dla polskiego, proces jako nieuprzywilejowany użytkownik `app`, tylko port 8000, sekrety wyłącznie przy uruchomieniu, `HEALTHCHECK` na `/health`.

```bash
docker build -t coi-formularze:0.1.0 .
docker run --rm -p 8000:8000 \
  -e JWT_SECRET="$(openssl rand -hex 32)" -e AUTH_PASSWORD=haslo \
  -e LLM_BASE_URL=http://host.docker.internal:11434/v1 \
  --add-host=host.docker.internal:host-gateway \
  coi-formularze:0.1.0
```

Całość razem z Ollamą (serwer Linux; GPU NVIDIA opcjonalnie, patrz komentarz w `docker-compose.yml`):

```bash
cp .env.example .env    # JWT_SECRET i AUTH_PASSWORD są wymagane
docker compose up -d --build
```

Przy pierwszym starcie `ollama-pull` pobiera `LLM_MODEL` do wolumenu `ollama` (kilkanaście GB), do tego czasu `/health` pokazuje `"llm": "model_missing"`. Na Macu wygodniej trzymać Ollamę natywnie (GPU) i uruchamiać samo API.

## Konfiguracja

Zmienne środowiskowe albo plik `.env` (wzór: `.env.example`).

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `MAX_FILE_SIZE_MB` | `10` | Maksymalny rozmiar pliku (MiB) |
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
| `OCR_ENGINE` | `tesseract` | `tesseract` albo `vlm` (model wizyjny w Ollamie) |
| `OCR_VLM_MODEL` | `qwen2.5vl:7b` | Model wizyjny dla `OCR_ENGINE=vlm` |

## REST API

| Metoda i ścieżka | Autoryzacja | Opis |
|---|---|---|
| `POST /api/auth/token` | brak | `username` i `password` (JSON lub formularz) → `{access_token, token_type, expires_in}` |
| `POST /api/forms/convert` | `Bearer` JWT | Jeden plik w polu `file` (multipart) → JSON COI, bez żadnej otoczki |
| `GET /health` | brak | `{"status": "ok", "llm": "ok" \| "model_missing" \| "unreachable" \| "no_api_key", "llm_backend": ..., "llm_model": ...}` |
| `GET /` | brak | Strona do wgrywania plików (kilka plików przetwarza po kolei, wynik jako drzewo JSON) |
| `GET /docs` | brak | OpenAPI / Swagger UI (przycisk „Authorize” loguje loginem i hasłem) |

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/token \
  -H 'Content-Type: application/json' -d '{"username": "admin", "password": "haslo"}' | jq -r .access_token)

curl -X POST http://localhost:8000/api/forms/convert \
  -H "Authorization: Bearer $TOKEN" -F "file=@wniosek.pdf" -o wniosek.json
```

Błędy zawsze jako JSON `{"detail": "..."}`:

| Kod | Kiedy |
|---|---|
| 400 | zła nazwa pliku, pusty plik, brak pola `file` albo więcej niż jeden plik |
| 401 | brak, zły lub wygasły token; zły login lub hasło |
| 413 | plik większy niż `MAX_FILE_SIZE_MB` |
| 415 | nieobsługiwane rozszerzenie albo zawartość niezgodna z rozszerzeniem |
| 422 | nie udało się odczytać formularza |
| 502 / 503 | model językowy zwrócił błąd / jest niedostępny |

## Walidacja i limity

- Rozszerzenia (wielkość liter bez znaczenia): `.pdf .doc .docx .ods .xlsx .jpg .jpeg .jpe .png`.
- Nazwa pliku: najwyżej 250 znaków, bez `; : > < / \ | ' * % $`.
- Typ MIME ustalany z zawartości (sygnatury), musi pasować do rozszerzenia: PDF `%PDF`, DOC plik OLE2 ze strumieniem `WordDocument`, DOCX/XLSX archiwum ZIP z `[Content_Types].xml` i katalogiem `word/` lub `xl/`, ODS archiwum z `mimetype` = `application/vnd.oasis.opendocument.spreadsheet`, JPEG `FF D8 FF`, PNG sygnatura PNG. Nagłówek `Content-Type` od klienta nie decyduje.
- Rozmiar: liczony w trakcie strumieniowego odczytu, odczyt przerywany po przekroczeniu limitu (nie ufamy samemu `Content-Length`).
- Pliki tylko w pamięci: treść żądania trafia prosto do RAM, nic nie jest zapisywane na dysk.

## Benchmark i wybór modelu

_Do uzupełnienia: wyniki benchmarku (9 formularzy × 8 formatów), porównanie modeli, czasy._

## Licencje

Wyłącznie open source bez copyleft: zależności Pythona na licencjach MIT, BSD, Apache-2.0, PSF i MIT-CMU (Pillow); `certifi` (zależność `httpx`) ma MPL-2.0, które obejmuje tylko jego własne pliki i nie wymaga publikacji naszego kodu. Tesseract: Apache-2.0, Ollama: MIT, modele Qwen3: Apache-2.0. Domyślnie model działa lokalnie (Ollama) i rozwiązanie nie korzysta z zewnętrznych API ani usług SaaS. Klient modelu mówi protokołem zgodnym z OpenAI, więc ten sam kod może użyć innego modelu o otwartych wagach na serwerze GPU albo u dostawcy (`LLM_BACKEND=openai`).
