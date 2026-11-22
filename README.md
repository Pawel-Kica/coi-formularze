# eFormatka (minimal)

A blank paper form in (PDF, scanned JPG/PNG, DOC, DOCX, ODS, XLS, XLSX), a COI e-service JSON ready to import out.
This is the minimal core of the HackNation 2026 "Cyfryzacja Formularzy" solution: the conversion pipeline and a
small REST API, without the clerk's editor, history, dictionary proposals, auth or benchmarks (those live on `main`).

## How it works

```
file ─► page images ─► Markdown ─► Plan ─► quote filter + repairs ─► builder ─► rules check ─► COI JSON
        (pypdfium2,    (Qwen3.5,   (GLM-5.2,  (app/quotes.py,          (app/builder/,  (app/rules.py)
        LibreOffice)   1 call/page) 2 passes)  app/repair.py)           deterministic)
```

The model understands the paper; code owns the COI format.

| Stage | Module | What it does |
|---|---|---|
| Read | `app/llm/transcribe.py` | Every format becomes page images (office files via LibreOffice headless), one vision call per page turns each into Markdown. An office file whose PDF lost text is read from its own text (`app/extract/`). |
| Plan | `app/llm/planner.py`, `app/plan.py` | Text-only model, two passes (structure, then conditions), into a typed Plan where every field, condition and statement carries a verbatim `quote` from the paper. |
| Filter | `app/quotes.py`, `app/repair.py` | Drops anything whose quote is not on the paper, broken conditions and fields of the standard pages; deterministic repairs (invented options, duplicates, required-when-shown). |
| Build | `app/builder/` | Plan → COI JSON: the standard COI pages (Dane, Kontakt i zgody, finish), address blocks, validators, registry links, flow. |
| Check | `app/rules.py` | COI JSON Schema + technical rules; a JSON that breaks them is never returned (422). |

The REST layer is `app/api.py` (+ `app/upload.py` for upload validation, `app/errors.py` for error mapping).

## Run locally

Requires Python 3.14+, [uv](https://docs.astral.sh/uv/), an Eden AI key, and LibreOffice for office formats
(`brew install --cask libreoffice`; PDF and images work without it).

```sh
cp .env.example .env        # set EDENAI_API_KEY
uv sync
uv run uvicorn app.api:app --reload --port 8000
```

Open http://localhost:8000 (upload page) or http://localhost:8000/docs.

```sh
curl -F file=@wniosek.pdf http://localhost:8000/api/forms/convert > wniosek.json
curl -F file=@wniosek.pdf 'http://localhost:8000/api/forms/convert?evidence=1'   # {coi, evidence}: paper quote per element
curl -H 'Content-Type: application/json' -d @wniosek.json http://localhost:8000/api/forms/check
curl http://localhost:8000/health
```

Each conversion calls Eden AI (one call per page plus two planner calls) and costs money.

Docker: `docker compose up -d --build` (API on :8000, reads `.env`).

## Tests

```sh
uv run pytest
```

No test calls the network (`tests/conftest.py` clears the key and blocks `httpx.post`). `gold/plans/` holds
hand-made plans of the 9 COI forms; the builder tests build each into a COI JSON that must pass the rules. Tests
that need the COI sample forms skip unless they are linked at `data/coi/`.
