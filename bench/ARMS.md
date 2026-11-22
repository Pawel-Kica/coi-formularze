# Simpler approaches vs ours (`bench/arms.py`)

Why our pipeline has the steps it has, in numbers: each arm drops part of it and runs on the same documents.
Results: the table at the end (3-judge panel, blind).

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
OUT=bench/runs/loop-arms
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

## Results (22.11)
Clerk corrections = the 3-judge panel of `bench/JUDGE-POPRAWKI.md` (2 of 3 count), blind: every output was judged
under a random name. Standard pages (Dane, Kontakt i zgody, final page) are not judged for any arm. Rule problems =
`app.rules.check` (the hard gate). Seconds and $ = the whole document (Eden catalog prices); arms that read our
Markdown include our reading (Qwen3.5: form 3 13 s $0.002, form 7 27 s $0.006, form 8 62 s $0.022). In brackets:
a second run of the same arm (v1 prompt, which lacked only "ASCII techName").

| Arm | Document | Rule problems | Clerk corrections | Seconds | $ |
| --- | --- | --- | --- | --- | --- |
| `file`: whole file -> Mistral Large 3 -> COI JSON | 3 pdf | 11 (1) | 3 | 71 | 0.027 |
| | 7 jpg | 1 (5) | 11 | 103 | 0.033 |
| | 8 pdf | 43 (49) | 16 | 166 | 0.044 |
| `images`: page images -> Qwen3.5 -> COI JSON | 3 pdf | 9 (504 after 803 s) | 7 | 232 | 0.070 |
| | 7 jpg | 6 | 13 | 761 | 0.079 |
| | 8 pdf | 5 | 18 | 641 | 0.056 |
| `md`: our Markdown -> GLM-5.2 -> COI JSON | 3 pdf | 0 (1) | 2 | 85 | 0.043 |
| | 7 jpg | 12 (2) | 1 (2) | 129 | 0.061 |
| | 8 pdf | 52 (14) | 19 (40) | 299 | 0.096 |
| `md --think`: the same, thinking on | 3 pdf | 0 | 0 | 69 | 0.041 |
| | 7 jpg | 4 | 1 | 218 | 0.055 |
| | 8 pdf | 7 | 21 | 318 | 0.088 |
| `nocode`: ours without quote filter and repairs | 3 / 7 / 8 | 0 / 0 / 0 | = ours / = ours / ~6 (not judged: output differs from ours by 1 field) | as ours | as ours |
| **ours**: Qwen3.5 Markdown -> GLM-5.2 plan (2 passes) -> code | 3 pdf | 0 | 0 | 26 | 0.011 |
| | 7 jpg | 0 | 1 (1) | 44 | 0.022 |
| | 8 pdf | 0 | 5 (4) | 267 (Eden slow at 05:05; ~100 in a calm run: reading 62 + planning 38) | 0.054 |

Sums over the 3 documents (the runs in the table, not the brackets): ours 6 corrections, 0 rule problems, 337 s,
$0.087; `md` 22, 64 problems, 513 s, $0.200; `md --think` 22, 11 problems, 605 s, $0.184; `file` 30, 55 problems, 340 s,
$0.104; `images` 38, 20 problems, 1634 s, $0.205.
