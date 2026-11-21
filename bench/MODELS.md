# Model and quality notes

## Decision

No model benchmarking (Paweł 21.11); local default `qwen3:30b-a3b-instruct-2507-q4_K_M`; later a stronger open-weights model via an OpenAI-compatible API (Eden AI). OCR: Tesseract with preprocessing (~97% on scans); vision models dropped (qwen2.5vl slow and drops content, qwen3-vl:8b empty output).

External model APIs (Eden included) are called only with Paweł's explicit approval for that specific run. All measurements below are local Ollama runs.

Prompts and code target a strong model: no prompt hacks or task splitting for a small model's weak spots. Code keeps the guarantees (JSON Schema output, quote filter, COI rules check, repair of malformed answers). A failure caused only by the local model is marked **ograniczenie modelu**.

## How to measure

    uv run python -m bench.run_all --forms 1,2,3,4,5,6,7 --formats pdf --out bench/runs/<name>

Columns: JSON ok (definition-of-done point 1), COI rules problems (point 2), quote-filter drops, `gold` = `score_plan` against `gold/plans/formN.json`, `ref` = `score_coi` recall against the COI reference JSON (forms 1 and 5, point 3), seconds per form (Ollama shared with other threads, so times are indicative). Drops include fields the filter removes because they belong to the pages code builds from the Standards (applicant, proxy, contact data); those are expected, not hallucinations.

Quote filter sanity check (no model): the gold plans run through `filter_plan` on the extracted text of all 69 form files drop **0** quotes on every format (pdf, doc, docx, ods, xlsx, jpg, jpeg, jpe, png).

## Run 1: `bench/runs/q30b-pdf` (forms 1-7, PDF, before the fixes below)

| Form | JSON | Rules | Drops (of them standard-page) | Gold | Ref recall (tabs / sections / fields / deps) | Time |
|---|---|---|---|---|---|---|
| 1 | ok | 0 | 3 (3) | 0.945 | 0.50 / 0.81 / 0.87 / 1.00, overall 0.89 | 49 s |
| 2 | ok | 0 | 20 (19) | 0.843 | - | 78 s |
| 3 | ok | 0 | 1 (0) | 0.553 | - | 54 s |
| 4 | ok | 0 | 8 (8) | 0.649 | - | 78 s |
| 5 | ok | 0 | 3 (0) | 0.703 | 0.75 / 0.83 / 0.87 / 0.94, overall 0.88 | 41 s |
| 6 | ok | 0 | 3 (3) | 0.750 | - | 153 s |
| 7 | ok | 0 | 2 (0) | 0.695 | - | 74 s |

7/7 JSON, 7/7 pass the COI rules. Gold plans built the same way score 1.00 ref recall on forms 1 and 5, so the gap is the plan, not the builder.

Failure modes:
- Invented boilerplate statement "Oświadczam, że podane informacje są prawdziwe (i pełne)" (forms 2, 3, 5): not on paper, not in the prompt; the quote filter drops it. ograniczenie modelu, already guarded.
- Rules the prompt already states but the model skipped (form 7: inline "hodowanie/utrzymywanie*" not a RADIO, "(metryka, numer rodowodu, imię, nazwisko i adres ...)" not split, blank next to "[ ] mikroprocesor ____" not a field, address not an address section; form 6: four look-alike table sections instead of one repeating section with "Rodzaj", addressee taken from the RODO clause; form 3: case fields missed). ograniczenie modelu.
- Nonsense condition (form 7: pedigree shown only for "Pies"); the quote is real, so the filter cannot catch it. ograniczenie modelu.
- Synthesized "Czy w sprawie występuje pełnomocnik?" RADIO (form 6) slipped past the standard-page filter: fixed in code (below).
- Scores understate some plans: form 5 is near-gold, but different wording of the choice label ("Jak chcesz, by została zrealizowana nadpłata?" vs "Wybierz, co mamy zrobić z nadpłatą") makes the scorer miss the field and both conditions, and a different page title loses the whole reference tab.

## Fixes after run 1 (model-agnostic)

- Extraction: DOCX and legacy DOC now read page headers and footers (form 7's fee and bank account live in the footer; form 4's "* niepotrzebne skreślić" too). This was the cause of the only gold quote drops (form 7 `.DOC`, 2), not quote normalisation.
- Quote filter: NFKC normalisation ("m²" = "m2", ligatures); any proxy field (`pełnomocni…`) is a standard-page field even as a choice question; "Dane identyfikacyjne współmałżonka" is no longer mistaken for the applicant block.
- Planner: pass-1 reminder block ("small models follow rules better") replaced by a one-line task after the form text; every schema enum is typed (`{"type": "string", "enum": ...}`, some providers turn a typeless enum into `{}`); `draft_to_plan` and `apply_conditions` repair or drop provider garbage instead of crashing.
- Prompt (forms 8 and 9, any model): calculation tables with one named category per row keep every row as its own fields (printed rates go to descriptions), repeating sections only for rows of the same kind or "take another copy for the next dog"; other persons (spouse) and a separate correspondence address stay in `pages` even when the paper marks them conditional; table quotes copy one cell, with the paper's word forms.
- Pass 2 sees where each field comes from on paper (`← papier: "<quote>"` in the plan listing) and is told to walk the paper's conditional sentences, not only the choice fields.
- `address: true` is kept only on sections whose fields are all address lines (the 30b flagged "Dane kandydata" with name and birth date; the builder would have replaced those fields with the address block).
- Client: `seed` only for Ollama and Mistral routes, `reasoning_effort: "none"` only for remote Qwen and Mistral; retries on 429 / 5xx / transport errors. Ollama's `/v1` enforces our JSON Schemas (enum, types, array bounds, no extra keys; checked on the 30b), so there is no native fallback.

## Runs 2 and 3 (forms 1-9, PDF, after the fixes)

Run 2 `bench/runs/q30b-pdf-2`: after the extraction, filter, schema and prompt fixes. Run 3 `bench/runs/q30b-pdf-3`: plus the pass-2 listing and the address guard. Ollama was shared with another thread during run 3, so its times are inflated.

| Form | JSON | Rules | Drops run 3 (standard-page / other) | Gold run 1 → 2 → 3 | Ref recall run 3 (tabs / sections / fields / deps) | Time run 2 / 3 |
|---|---|---|---|---|---|---|
| 1 | ok | 0 | 0 / 0 | 0.945 → 0.978 → 0.960 | 0.50 / 0.81 / 0.87 / 1.00, overall 0.89 | 55 / 81 s |
| 2 | ok | 0 | 19 / 0 | 0.843 → 1.000 → 1.000 | - | 70 / 125 s |
| 3 | ok | 0 | 0 / 4 | 0.553 → 0.553 → 0.553 | - | 51 / 131 s |
| 4 | ok | 0 | 9 / 3 | 0.649 → 0.741 → 0.741 | - | 54 / 104 s |
| 5 | ok | 0 | 0 / 3 | 0.703 → 0.960 → 0.960 | 0.75 / 0.83 / 0.87 / 0.94, overall 0.88 | 30 / 70 s |
| 6 | ok | 0 | 9 / 1 | 0.750 → 0.841 → 0.736 | - | 105 / 148 s |
| 7 | ok | 0 | 0 / 1 | 0.695 → 0.648 → 0.648 | - | 94 / 156 s |
| 8 | ok | 0 | 6 / 0 | - → 0.597 → 0.597 | - | 263 / 469 s |
| 9 | ok | 0 | 16 / 0 | - → 0.578 → 0.578 | - | 407 / 451 s |

9/9 JSON, 9/9 pass the COI rules. Mean gold score forms 1-7: 0.734 (run 1) → 0.800 (run 3). Form 6 moved 0.84 → 0.74 between runs 2 and 3 with pass 1 unchanged: Ollama is not bit-deterministic under concurrent load, so treat about ±0.1 per form as noise.

Other (non-standard) drops in run 3, all correct removals: invented "Oświadczam, że podane informacje są prawdziwe" (forms 3, 5), empty attachment lines "2) ____" (form 5), a bare "w ____" quote (form 4), conditions whose trigger comes after the target (form 3, 3x), whose trigger was a dropped standard-page field (forms 4, 6) or sits inside the target (form 7).

Pass 2 A/B on the run-2 pass-1 plans (`conditions recall / precision`): form 8 0.00/0.00 → 0.07/0.33, form 9 0.10/0.17 → 0.20/0.11 (19 conditions, each section of the "3 in 1" form shown only for its application type: correct for that plan, but the gold merges the shared parts), form 5 1.00/1.00 both, form 7 0.00 both.

Forms 8 and 9 (30b), what is left:
- Form 8: calculation table now complete row by row (5 + 5 household counts, no squeezing), but result and sum fields skipped; spouse section (B.2) skipped; the four numbered "Okoliczności" options became four dates without the choice field, so their conditions cannot exist; attachments "Załącznik A TAK NIE" as Tak/Nie RADIOs instead of FILE_UPLOADER; no trigger question for "adres do korespondencji (wypełnić tylko w przypadku kiedy jest inny)". All are rules the prompt states: ograniczenie modelu.
- Form 9: the 30b repeats the shared sections per application type instead of one set with SHOW conditions (valid, but 3x longer); the correspondence channel choice (poczta / ePUAP / e-Doręczenia) is missed; addressee "Prezydent Miasta Zgierza" vs gold "Urząd Miasta Zgierza" (the paper has both). Structure is a design choice the prompt does not force; the rest ograniczenie modelu.
- Scorer caveat: `score_plan` matches conditions through matched field labels, so a different pass-1 structure caps condition recall even when the conditions are right.

Open "ograniczenie modelu" items (prompt already covers them, the 30b skips them): inline "a/b*" choices and parenthetical compound fields not split (form 7), blanks next to options (form 7), look-alike table sections not merged into one repeating section (form 6), addressee from the RODO clause (form 6), spouse section and numbered-option choice fields (form 8), attachments as Tak/Nie radios (form 8), invented boilerplate statement (forms 2, 3, 5; filtered).
