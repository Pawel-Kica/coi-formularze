# Simpler approaches vs ours (`bench/arms.py`)

Why our pipeline has the steps it has, in numbers: each arm drops part of it and runs on the same documents.
Results and the verdict: `~/hacknation-2026/resources/loop/arms.md`.

| Arm | What goes in | Model | What comes out |
| --- | --- | --- | --- |
| `file` | the whole file (PDF as an Eden `file` part, JPG/PNG as one image) | `mistral/mistral-large-2512` (open weights, takes files) | COI JSON, one call |
| `images` | page images, the same ones our reader gets (1024 px) | Qwen3.5-397B-A17B | COI JSON, one call |
| `md` | our Qwen3.5 Markdown of the pages (cached, `--md`) | GLM-5.2 | COI JSON, one call |
| `md --think` | the same, thinking on (`reasoning_effort: high`, 120k tokens) | GLM-5.2 | COI JSON, one call |
| `nocode` | our cached plan before the quote filter and repairs (`--raw`) | none | builder only |
| ours | `bench.run_all` / `app.pipeline` | Qwen3.5 reads, GLM-5.2 plans (2 passes) | quote filter, repairs, builder |

`read` measures only our reading (Qwen3.5, page images -> Markdown): the seconds and $ the `md` arms share with ours.

## The prompt of the direct arms
One system prompt for `file`, `images` and `md`: `bench/arms_direct.md` (the task in 6 lines) plus the COI task
documents read from `data/coi/Materiały do zadania dla Uczestników/` at run time: technical rules, JSON
description, JSON schema, dictionaries, Standards chapter 2 (chapter 1 is tone of voice), and the form 1 reference
JSON as the example (~83k characters, ~30k tokens). The user message is the form (file part, page images or
Markdown) plus one sentence. Temperature 0, JSON object mode, thinking off as for every Eden number (except
`--think`), 60k output tokens. An answer that is not one JSON object counts as failed (code fences and text around
it are cut off first). `meta.json` sorts the COI rule problems into `mechanical` (ids, techNames), `conditions`
(jsonata) and `structure`.

## Rerun
```
OUT=/Users/main/coi-formularze/bench/runs/loop-arms
F3="$(ls data/coi/3.*/*.pdf)"
uv run python -m bench.arms --arm file   --file "$F3" --out $OUT
uv run python -m bench.arms --arm images --file "$F3" --out $OUT
uv run python -m bench.arms --arm md     --file "$F3" --md <run>/coi/3/pdf.text.md --out $OUT [--think]
uv run python -m bench.arms --arm nocode --file "$F3" --raw <run>/<file>.raw.json --out $OUT
uv run python -m bench.arms --arm read   --file "$F3" --out $OUT
```
Each run writes `$OUT/<arm>[-think]/<form>-<ext>.{coi.json,raw.txt,meta.json}` (meta: seconds, $, tokens, COI rule
problems, element counts). `file`, `images`, `md` and `read` call Eden (2-8 cents per file); `nocode` does not.

Clerk corrections: `bench/poprawki.py kit <arm>/<form>-<ext>.coi.json --form N` per output (we copied each output
under a random name first, so the judges could not tell the arms apart), the 3-judge panel of
`bench/JUDGE-POPRAWKI.md`, then `poprawki.py count`.
