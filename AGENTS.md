# coi-formularze

Paweł's HackNation 2026 solution to the COI "Cyfryzacja Formularzy" task: a REST API that turns a blank paper form (PDF, DOC, DOCX, ODS, XLSX, XLS, JPG, PNG) into an importable COI e-service JSON. Everything runs locally: an open-weights model in Ollama, no cloud.

## Hard rules
- **NEVER call Eden AI or any external model API** (no runs, smoke tests or measurements) without Paweł's explicit OK for that specific run. `.env` holds no key; never open, print, log or commit `.env`.
- The local Ollama model (`qwen3:30b-a3b-instruct-2507-q4_K_M`) has 2 parallel slots (32k context each) shared by every thread, the dev server and benches: run small, expect queueing beyond 2 requests (a form takes 40-150 s). Ollama runs as launchd agent `com.pawel.ollama` (log `~/Library/Logs/ollama.log`, restart `launchctl kickstart -k gui/$(id -u)/com.pawel.ollama`).
- The Mac is at its memory limit. Only ONE model-heavy bench/eval at a time across all threads: check `pgrep -fl "bench\.|eval\.py|eval_all"` first; if one runs, do non-model work or wait. No full-set runs unless that is your job. Never load a second Ollama model next to the running one. Model exploration (other models, a small VLM, Docling) is open again (Paweł 21.11 ~19:10), but only by the thread that owns it, one run at a time. Don't start dev servers, Playwright, Chrome or extra Python workers you don't need, and stop the ones you started.
- Bonus II (dictionaries) owns `app/dictionaries.py`, `app/llm/dictionaries.py`, `gold/dictionaries/`, `bench/dictionaries.py`, `tests/test_dictionaries.py` and its block in `app/api.py` and `app/static/index.html`; never rewrite that block.
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
- `app/api.py` REST: `POST /api/auth/token`, `POST /api/forms/convert`, `POST /api/dictionaries/proposals`, `GET /health`, `/` upload page, `/preview`; dev-only `/review` (`app/review.py`, 404 unless `REVIEW_RUNS_DIR` is set).
- `app/config.py` settings from env/`.env` (`from app.config import settings`); `app/security.py` JWT (own issuer or JWKS); `app/upload.py` in-memory upload, size cap, name/extension/magic-byte checks.
- `app/plan.py` the Plan contract between model and builder; every field, condition and statement carries a `quote` from the paper.
- `app/extract/` bytes → normalized text: `pdf.py` (pages with a missing or garbled text layer go to OCR), `doc.py` (own Word 97 parser), `docx.py`, `sheets.py` (XLSX/XLS/ODS), `ocr.py` + `scan.py` (Tesseract with preprocessing), `layout.py`, `normalize.py` (same form, any format → same text).
- `app/llm/client.py` one OpenAI-compatible JSON-Schema chat call (`LLM_BACKEND`, `LLM_BASE_URL`, `LLM_MODEL`, optional `images` as `image_url` data-URL parts); `planner.py` two passes (structure, then conditions); `prompts/` prompt texts and examples.
- `app/llm/vision.py` page images for a vision model, behind `LLM_VISION` (default off = text-only requests, unchanged): PDF pages (pypdfium2) and JPG/PNG (stacked pages cut) at `LLM_VISION_MAX_SIDE` 1400 px, first `LLM_VISION_MAX_PAGES` 8; both passes get them before the text, pass 2 at `LLM_VISION_CONDITIONS_SIDE` 1024 (0 = none), plus `prompts/vision.md` / `vision_conditions.md`. Quotes still come from the text. `LLM_VISION_TEXT=0` = images instead of the text when every page has one (`vision.images_only`, prompts `vision_only*.md`; quotes from the model's reading, filter at `THRESHOLD_OCR` via `quotes.threshold_for`); `LLM_VISION_GRAY=1` = grayscale pages. `OCR_VLM_MODEL` unset = `LLM_MODEL`.
- `app/quotes.py` drops plan items whose quote is not on paper, and broken conditions.
- `app/builder/` Plan → COI JSON, deterministic: `core.py` building blocks, `standard.py` predefined pages from the COI Standards (Dane, Kontakt i zgody, finish), `texts.py` verbatim HTML texts.
- `app/rules.py` COI JSON Schema + "Reguły techniczne" checker, `check(coi)` → list of problems.
- `app/dictionaries.py` + `app/llm/dictionaries.py` bonus II: new dictionary proposals next to the COI JSON, model proposes, code filters.
- `app/static/index.html` upload page (history, batch, ZIP); `app/static/preview.html` e-service preview (bonus V); `app/static/review.html` dev-only "Przegląd": a finished `run_dir.py` run, judge verdicts bad first, original next to our preview, "zgadzam się / nie zgadzam się" + note appended to `<run>/judge/human.jsonl` (file, agree, note, verdict, time). `app/text.py` shared text helpers.
- `bench/` `run_all.py` end-to-end definition-of-done table, `run_dir.py` the same pipeline over any folder (2 files at a time, saves text/plan/COI per file and `source.txt` = the source folder, which `/review` needs) for black-box sets a Claude judge reviews, `judge_kit.py <run_dir> <src>` per-file judge kit (`<run_dir>/judge/<file>/`: page PNGs, `extracted.txt`, `original.txt` via textutil for DOC/DOCX, `ours.md` = our COI JSON as an outline), `JUDGE.md` the judge brief (Polish, dobrze/źle + category), `judge_report.py <run_dir> [--add]` validates and appends verdicts to `judge/verdicts.jsonl`, writes `judge/REPORT.md` worst first, `matrix.py` input matrix (named arms of env settings from `matrix_arms.json`, each a `run_all` child into `bench/runs/matrix-<time>/<arm>/`, one model loaded at a time, table `matrix.md`), `score.py` scorer (plan vs gold, COI vs reference), `run_models.py` model comparison (not allowed now), `extract_report.py` extraction/OCR consistency, `dictionaries.py` bonus II bench, `MODELS.md` measurements and decisions; runs go to `bench/runs/` (gitignored).
- `gold/plans/formN.json` Claude's gold plans for all 9 forms, `gold/notes/formN.md` the reasoning for the 7 forms without a COI reference (2-4, 6-9) (TEXT_BLOCK: `label` = heading, `description` = text); `gold/dictionaries/` bonus II key (9 COI + BIP forms); `gold/coi/formN.json` COI references 1 and 5 with hand-added validation (only `validation` differs, decisions in `gold/coi/NOTES.md`), scored by `walid=`.
- `tests/` pytest per module. `output/` deliverable JSONs (empty until submission). `data/coi` symlink to `~/hacknation-2026/resources/formularze` (the 9 COI form packs, references, Standards; gitignored).

## Commands
- Tests: `uv run pytest`
- End to end: `uv run python -m bench.run_all [--forms 1,5] [--formats pdf] [--bip] [--out bench/runs/<name>]` (uses the local model); `--rebuild --out <earlier run>` re-runs only the builder on cached plans, never the model (files without one: "not cached"); forms 1 and 5 print `walid=` (validator F1 vs `gold/coi/`, matched fields only, `(+N unscored)` = gold validators on unmatched fields) next to `ref=`
- Input matrix (model x vision input, uses the local model, one arm after another): `uv run python -m bench.matrix --screen` (forms 1,4,5,7, PDF + PNG) or without `--screen` (9 forms); `--arms vl-img,vl-img-gray`, `--formats`, `--resume bench/runs/matrix-<time>` skips done files; stops other Ollama models before each arm and refuses to start next to another bench (`--force`)
- Score one file: `uv run python bench/score.py coi|plan <ours.json> <ref.json>`
- Dev server: launchd agent `com.pawel.coi-api` keeps the API on http://localhost:8010 (login admin / haslo, `--reload` on `app/`), log `~/Library/Logs/coi-api.log`. Restart: `launchctl kickstart -k gui/$(id -u)/com.pawel.coi-api` (after a plist env change: `launchctl bootout gui/$(id -u)/com.pawel.coi-api`, then `bootstrap gui/$(id -u) <plist>`, kickstart keeps the old env). Its plist sets `REVIEW_RUNS_DIR=~/coi-formularze/bench/runs`, so the review page is http://localhost:8010/review (unset in Docker). Never put `--` inside an XML comment in its plist (a heartbeat check parses it with plistlib).
- Native run, Docker, config table, API: `README.md` (jury-facing, keep it human).

## Where decisions live
- Project decisions (index): `~/hacknation-2026/repo/CONTEXT.md`, details in `~/hacknation-2026/repo/docs/decisions/` (task, architecture, model and Eden, bonuses, quality bar and validation).
- COI task docs (task PDF, Standards, technical rules, dictionaries, reference JSONs): `data/coi/`, browsable at https://hacknation-2026-sand.vercel.app/admin.html.
- Black-box test forms from gmina BIPs: `~/hacknation-2026/resources/bip-formularze/`.
- Model and quality notes: `bench/MODELS.md`; per-form gold reasoning: `gold/notes/`.
