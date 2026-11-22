# eFormatka

A blank paper form in (PDF, scanned JPG/PNG, DOC, DOCX, ODS, XLS, XLSX), a COI e-service JSON ready to import out,
plus a web app where a clerk checks and fixes it next to the original. HackNation 2026, task COI "Cyfryzacja
Formularzy". This branch holds the product only; the research tooling (benchmarks, AI judges, docs) lives on `main`.

## Repository layout

```
backend/                 Python 3.14, FastAPI, uv
  app/
    main.py              the FastAPI app: mounts the API and serves the frontend pages
    api/                 HTTP layer only: one router per resource, auth, upload checks, error mapping
    services/            use cases: the conversion pipeline, dictionary proposals
    llm/                 Eden AI client, page transcription, planner, prompts
    grounding/           keeps only what the paper backs: quote filter, deterministic repairs
    coi/                 the COI format, owned by code: builder, rules, Polish problem messages
    document/            the uploaded original: where each quote sits ("Źródło"), its headings, office -> PDF
    extract/             a file's own text (office files, PDF text layer)
    storage/             SQLite: upload history, dictionary catalog
    core/                settings (.env), the Plan contract, text helpers
  tests/                 pytest, no network; fixtures/plans = hand-made plans of the 9 COI forms
examples/                two sample blank forms to try the API with
frontend/                static pages, no build step; they call the API on the same origin
  index.html             upload page: history, original next to the e-service, JSON, dictionaries
  preview.html           the e-service as a citizen sees it, with the clerk's layer (quotes, conditions, "Źródło")
  editor.js              the "Edycja" sidebar of the preview
  dictionaries.html      dictionary catalog
Dockerfile, docker-compose.yml, .env.example
```

Dependencies point one way, with no cycles: `api` -> `services` -> `llm`, `document`, `grounding`, `coi`,
`storage` -> `extract`, `core`. Only `api` knows about HTTP, so the pipeline runs the same from a script
(`from app.services.pipeline import convert`).

## How a form is converted

```
file -> page images -> Markdown -> Plan -> quote filter + repairs -> builder -> rules check -> COI JSON
```

| Stage | Module | What it does |
|---|---|---|
| Read | `llm/transcribe.py` | Every format becomes page images (office files via LibreOffice headless), one vision call per page (Qwen3.5) turns each into Markdown. An office file whose PDF lost text is read from its own text (`extract/`). |
| Plan | `llm/planner.py`, `core/plan.py` | Text-only model (GLM-5.2), two passes (structure, then conditions), into a typed Plan where every field, condition and statement carries a verbatim `quote` from the paper. |
| Filter | `grounding/quotes.py`, `grounding/repair.py` | Drops anything whose quote is not on the paper, broken conditions and fields of the standard pages; deterministic repairs (invented options, duplicates, required-when-shown). |
| Build | `coi/builder/` | Plan -> COI JSON: the standard COI pages, address blocks, validators, registry links, flow. |
| Check | `coi/rules.py` | COI JSON Schema + technical rules; a JSON that breaks them is never returned (422). |

The model understands the paper; code owns the COI format.

## Quick start (Docker)

```sh
echo "EDENAI_API_KEY=your-key" > .env      # the only setting that must be set (see .env.example)
docker compose up -d --build --wait         # first build: about 3 minutes (LibreOffice in the image)
curl -X POST http://localhost:8000/api/forms/convert -F "file=@examples/formularz-3.jpeg" -o formularz-3.json
```

After about 20 s `formularz-3.json` holds the COI JSON of a scanned "Wniosek o wydanie zaświadczenia"; a second
sample is `examples/formularz-2.pdf` (judges counted 0 clerk corrections on both). The clerk's page is
http://localhost:8000. Compose passes `.env` to the container at start (`env_file`); it is never copied into the
image. After changing the key: `docker compose up -d`. Stop: `docker compose down`.

## Run locally

Requires [uv](https://docs.astral.sh/uv/) (it fetches Python 3.14+), an Eden AI key, LibreOffice for office formats
(`brew install --cask libreoffice`; PDF and images work without it) and Tesseract with Polish data for "Źródło" on
scans (`brew install tesseract tesseract-lang`).

```sh
cp .env.example .env        # set EDENAI_API_KEY; read from the repo root
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000 (upload page) or http://localhost:8000/docs (API). Each conversion calls Eden AI (one call
per page plus two planner calls) and costs money.


## REST API

| Endpoint | What it does |
|---|---|
| `POST /api/forms/convert` | multipart `file` -> COI JSON; `?evidence=1` -> `{coi, evidence}` (paper quote per element) |
| `POST /api/forms/jobs`, `GET /api/forms/jobs/{id}[/result]` | the same conversion in the background, to poll |
| `POST /api/forms/check` | COI JSON -> rules problems in Polish with the element id |
| `GET /api/forms/blank`, `POST /api/forms/text` | a form to build by hand; an office file's text |
| `/api/history...` | upload page history, original files, office PDFs, "Źródło" places and page images |
| `/api/dictionaries...` | dictionary catalog; `POST /api/dictionaries/proposals` proposes new dictionaries for a form |
| `GET /api/auth/config`, `POST /api/auth/token` | optional JWT login (`AUTH_ENABLED=1`) |
| `GET /health` | liveness and whether the key is set (never calls Eden) |

Settings: `.env.example`.

## Tests

```sh
cd backend
uv run pytest            # fast, skips the Tesseract tests
uv run pytest -m ""      # everything
```

No test calls the network (`tests/conftest.py` clears the key and blocks `httpx.post`). Tests that need the COI
sample forms skip unless they are linked at `backend/data/coi/`.
