# coi-formularze

Paweł's HackNation 2026 solution to the COI "Cyfryzacja Formularzy" task: a REST API that turns a blank paper form (PDF, DOC, DOCX, ODS, XLSX, XLS, JPG, PNG) into an importable COI e-service JSON, plus the clerk's web app. Models are remote open-weights models on Eden AI: `TRANSCRIBE_MODEL` Qwen3.5-397B-A17B reads page images into Markdown, `LLM_MODEL` GLM-5.2 plans from that Markdown (text only).

## Hard rules
- **Eden calls cost money.** For development: up to 2 files per task through the app without asking (one file = one conversion); more only on Paweł's word. `pytest` never calls the network (`backend/tests/conftest.py` clears the key and blocks `httpx.post`). `.env` (repo root) holds `EDENAI_API_KEY`; never open, print, log or commit it.
- New commits: author Paweł Kica only, no AI co-author lines (the local `.git/hooks/commit-msg` strips them; the 8 commits that brought this layout from branch `minimal` are a teammate's). Push to `main`.
- Never delete files: `mv <path> /tmp/`.
- Prompts and code target a strong model: no workarounds for one model's weak spots.
- Don't edit `backend/app/llm/prompts/*.md` unless prompt work is your task.
- `README.md` is jury-facing and in Polish (the task requires it to describe the Docker build, running, configuration and the REST API); `backend/tests/test_readme.py` checks that every `/api` route is in it and every in-page link lands. Code, comments and commits stay in English.
- `output/` holds the deliverable COI JSONs for forms 2-4 and 6-9 (task point 4); never regenerate or edit them by hand.
- The research tooling (bench, gold plans, AI judges, run history) lives on branch `badania`, frozen at the state the README numbers come from.

## Layout
- `README.md` "Struktura repozytorium" is the map. Pipeline (`backend/app/services/pipeline.py`): file → page images → Markdown (`llm/transcribe.py`) → GLM-5.2 plan in 2 passes (`llm/planner.py`, contract `core/plan.py`) → quote filter + repairs (`grounding/`) → builder (`coi/builder/`) → rules (`coi/rules.py`). The model understands the paper, code owns the COI format.
- Dependencies point one way: `api` → `services` → `llm`, `document`, `grounding`, `coi`, `storage` → `extract`, `core`. Only `api` knows HTTP.
- `frontend/` static pages served by `backend/app/main.py` (`FRONTEND_DIR`): `index.html` upload page, `preview.html` e-service preview, `editor.js` the "Edycja" sidebar, `dictionaries.html` catalog.
- `docs/` jury docs linked from README: `historia.md`, `img/` diagrams as HTML next to PNG (`python3 docs/img/render.py [name]`, numbers in `img/liczby.json`).
- `backend/data/coi` (gitignored): symlink to `~/hacknation-2026/resources/formularze`, the 9 COI form packs; tests that need them skip without it.

## Commands
- Tests: `cd backend && uv run pytest` (fast, skips the Tesseract tests marked `slow`); everything `uv run pytest -m ""`.
- Run: `cd backend && uv run uvicorn app.main:app --port 8000` (reads `.env` from the repo root). Docker: `docker compose up -d --build --wait`.
- Dev server: launchd agent `com.pawel.coi-api` keeps it on http://localhost:8010 (`app.main:app --reload` from `backend/`, history in `backend/history.sqlite3` + `backend/history-pdf/`), log `~/Library/Logs/coi-api.log`. Restart: `launchctl kickstart -k gui/$(id -u)/com.pawel.coi-api`; after a plist change `launchctl bootout gui/$(id -u)/com.pawel.coi-api`, then `bootstrap gui/$(id -u) <plist>`. Never put `--` inside an XML comment in its plist.

## Where decisions live
- Project decisions: `~/hacknation-2026/repo/CONTEXT.md`, details in `~/hacknation-2026/repo/docs/decisions/`.
- COI task docs: `backend/data/coi/`, browsable at https://hacknation-2026-sand.vercel.app/admin.html.
