# eFormatka (minimal)

A blank paper form in (PDF, scanned JPG/PNG, DOC, DOCX, ODS, XLS, XLSX), a COI e-service JSON ready to import out.
This is the HackNation 2026 "Cyfryzacja Formularzy" solution with every product feature (the conversion pipeline,
the REST API, the clerk's web app) and without the research tooling of `main` (benchmarks, AI judges, docs, the
deliverable JSONs, the dev-only review page).

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

Around the pipeline:

| Feature | Module |
|---|---|
| REST API (convert, background jobs, check, blank form, office text) | `app/api.py`, `app/jobs.py`, `app/upload.py`, `app/errors.py`, `app/problems.py` |
| Upload page: history, original next to the e-service, JSON view | `app/static/index.html`, `app/history.py` (SQLite) |
| E-service preview with the clerk's editor sidebar | `app/static/preview.html`, `app/static/editor.js` |
| "Źródło": the paper crop behind each element; "W dokumencie": its paper heading | `app/sources.py`, `app/headings.py` |
| New dictionary proposals and the dictionary catalog | `app/dictionaries.py`, `app/llm/dictionaries.py`, `app/catalog.py`, `app/static/dictionaries.html` |
| Optional JWT auth (`AUTH_ENABLED=1`) | `app/security.py` |

## Run locally

Requires Python 3.14+, [uv](https://docs.astral.sh/uv/), an Eden AI key, LibreOffice for office formats
(`brew install --cask libreoffice`; PDF and images work without it) and Tesseract with Polish data for "Źródło" on
scans (`brew install tesseract tesseract-lang`).

```sh
cp .env.example .env        # set EDENAI_API_KEY
uv sync
uv run uvicorn app.api:app --reload --port 8000
```

Open http://localhost:8000 (upload page) or http://localhost:8000/docs. Settings: `.env.example`.

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
uv run pytest            # fast, skips the Tesseract tests
uv run pytest -m ""      # everything
```

No test calls the network (`tests/conftest.py` clears the key and blocks `httpx.post`). `gold/plans/` holds
hand-made plans of the 9 COI forms; the builder tests build each into a COI JSON that must pass the rules. Tests
that need the COI sample forms skip unless they are linked at `data/coi/`.
