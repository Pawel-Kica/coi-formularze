# coi-formularze

Paweł's HackNation 2026 solution to the COI "Cyfryzacja Formularzy" task: a REST API that turns a blank paper form (PDF, DOC, DOCX, ODS, XLSX, JPG, PNG) into an importable COI e-service JSON. Everything runs locally: an open-weights model in Ollama, no cloud.

## Hard rules
- **NEVER call Eden AI or any external model API** (no runs, smoke tests or measurements) without Paweł's explicit OK for that specific run. `.env` holds no key; never open, print, log or commit `.env`.
- The local Ollama model (`qwen3:30b-a3b-instruct-2507-q4_K_M`, `-np 1`) serves one request at a time and is shared by every thread, the dev server and benches: run small, expect queueing (a form takes 40-150 s).
- Shared working tree: other threads have uncommitted work here. Commit only your own paths (`git commit -- <paths>`), never `git stash`, `reset`, `checkout .` or rewrite others' hunks; re-read a file before editing it.
- Commits: author Paweł Kica only, no Claude lines (the local `.git/hooks/commit-msg` strips them). Push to `main`.
- Never delete files: `mv <path> /tmp/`.
- Prompts and code target a strong model: no workarounds for the small model's weak spots. A failure caused only by the local model is marked "ograniczenie modelu" (`bench/MODELS.md`).
- Don't edit `app/llm/prompts/*.md` unless prompt work is your task.
- Subagents via T3 Code `delegate_task` (visible to Paweł), not the native Agent tool.
- Blocked on Paweł (product decision): `notify "<what you need>"` (Telegram, plain text, no secrets), keep working on what it doesn't block. Timeline and submission are his: don't plan them or ask. Don't ask him to start Docker until the core and quality loop are green.

## Pipeline (`app/pipeline.py`)
file → text (`app/extract/`) → model plan (`app/llm/planner.py`, contract `app/plan.py`) → quote filter (`app/quotes.py`) → builder (`app/builder/`, Plan → COI JSON) → rules check (`app/rules.py`). The model understands the paper, code owns the COI format.

## Layout
- `app/api.py` REST: `POST /api/auth/token`, `POST /api/forms/convert`, `POST /api/dictionaries/proposals`, `GET /health`, `/` upload page, `/preview`.
- `app/config.py` settings from env/`.env` (`from app.config import settings`); `app/security.py` JWT (own issuer or JWKS); `app/upload.py` in-memory upload, size cap, name/extension/magic-byte checks.
- `app/plan.py` the Plan contract between model and builder; every field, condition and statement carries a `quote` from the paper.
- `app/extract/` bytes → normalized text: `pdf.py`, `doc.py` (own Word 97 parser), `docx.py`, `sheets.py` (XLSX/ODS), `ocr.py` + `scan.py` (Tesseract with preprocessing), `layout.py`, `normalize.py` (same form, any format → same text).
- `app/llm/client.py` one OpenAI-compatible JSON-Schema chat call (`LLM_BACKEND`, `LLM_BASE_URL`, `LLM_MODEL`); `planner.py` two passes (structure, then conditions); `prompts/` prompt texts and examples.
- `app/quotes.py` drops plan items whose quote is not on paper, and broken conditions.
- `app/builder/` Plan → COI JSON, deterministic: `core.py` building blocks, `standard.py` predefined pages from the COI Standards (Dane, Kontakt i zgody, finish), `texts.py` verbatim HTML texts.
- `app/rules.py` COI JSON Schema + "Reguły techniczne" checker, `check(coi)` → list of problems.
- `app/dictionaries.py` + `app/llm/dictionaries.py` bonus II: new dictionary proposals next to the COI JSON, model proposes, code filters.
- `app/static/index.html` upload page (history, batch, ZIP); `app/static/preview.html` e-service preview (bonus V). `app/text.py` shared text helpers.
- `bench/` `run_all.py` end-to-end definition-of-done table, `score.py` scorer (plan vs gold, COI vs reference), `run_models.py` model comparison, `extract_report.py` extraction/OCR consistency, `dictionaries.py` bonus II bench, `MODELS.md` measurements and decisions; runs go to `bench/runs/` (gitignored).
- `gold/plans/formN.json` Claude's gold plans for all 9 forms, `gold/notes/formN.md` the reasoning per form (TEXT_BLOCK: `label` = heading, `description` = text); `gold/dictionaries/` bonus II key (9 COI + BIP forms); `gold/coi/` patched COI references 1 and 5 with validation (in progress).
- `tests/` pytest per module. `output/` deliverable JSONs. `data/coi` symlink to `~/hacknation-2026/resources/formularze` (the 9 COI form packs, references, Standards; gitignored).

## Commands
- Tests: `uv run pytest`
- End to end: `uv run python -m bench.run_all [--forms 1,5] [--formats pdf] [--bip] [--out bench/runs/<name>]` (uses the local model)
- Score one file: `uv run python bench/score.py coi|plan <ours.json> <ref.json>`
- Dev server: launchd agent `com.pawel.coi-api` keeps the API on http://localhost:8010 (login admin / haslo, `--reload` on `app/`), log `~/Library/Logs/coi-api.log`. Restart: `launchctl kickstart -k gui/$(id -u)/com.pawel.coi-api`. Never put `--` inside an XML comment in its plist (a heartbeat check parses it with plistlib).
- Native run, Docker, config table, API: `README.md` (jury-facing, keep it human).

## Where decisions live
- Project decisions (index): `~/hacknation-2026/repo/CONTEXT.md`, details in `~/hacknation-2026/repo/docs/decisions/` (task, architecture, model and Eden, bonuses, quality bar and validation).
- COI task docs (task PDF, Standards, technical rules, dictionaries, reference JSONs): `data/coi/`, browsable at https://hacknation-2026-sand.vercel.app/admin.html.
- Black-box test forms from gmina BIPs: `~/hacknation-2026/resources/bip-formularze/`.
- Model and quality notes: `bench/MODELS.md`; per-form gold reasoning: `gold/notes/`.
