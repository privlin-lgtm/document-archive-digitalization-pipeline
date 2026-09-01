# Evaluation harness

Measures OCR and entity-extraction quality against a small hand-labeled
ground-truth set, so a pipeline change (swapping OCR backends, tuning a
threshold, adjusting a regex) can be judged objectively rather than by eye.

Deliberately standalone: it calls `ocr.preprocess`, `ocr.layout`,
`ocr.engine`, and `extraction.entities` directly — no Postgres, no Redis,
no `pipeline.run`/`worker.py`. It's a measurement tool, not a production
code path.

## Running it

```bash
python -m eval.report                    # eval/fixtures (checked in)
python -m eval.report path/to/fixtures    # a different fixture set
```

Needs a real `tesseract` binary on PATH. If you don't have one installed
locally, run it in the project's Docker image (which does):

```bash
docker compose run --rm app python -m eval.report
```

## Metrics

- **CER** (character error rate) / **WER** (word error rate): Levenshtein
  edit distance between the predicted and reference text, divided by the
  reference's length (characters for CER, words for WER). Both texts are
  whitespace-normalized first (collapsed to single spaces) so how OCR joins
  regions/lines doesn't affect the score — only actual recognition errors
  do. Reported per example and as a mean across the whole set.
- **Precision / recall / F1 per entity type**: predicted `(type,
  normalized_value)` pairs are matched against reference pairs by exact
  equality (multiset-aware — a duplicate prediction can match a duplicate
  reference, but not the same reference entity twice), aggregated across
  the *whole* fixture set (micro-averaged, not per-example) since most
  individual examples only have one or two entities of a given type.

## Ground-truth format

`fixtures/ground_truth.json` is a JSON array; each entry pairs one sample
image with its correct text and correct entities:

```json
{
  "id": "unique-slug",
  "image": "filename.png",
  "text": "expected full OCR text, written as one logical line",
  "entities": [
    {"type": "person", "value": "John A. Smith"},
    {"type": "location", "value": "Mumbai"},
    {"type": "date", "value": "1897-03-03"},
    {"type": "amount", "value": "USD 128.50"}
  ]
}
```

`image` is relative to `fixtures/images/`. `entities[].value` must match
`extraction.entities`' own normalization conventions exactly, since scoring
is exact-match:

- `person` — the raw name text, whitespace-collapsed (see
  `extraction.entities.extract_entities`'s person branch)
- `location` — the gazetteer-canonical name (e.g. "Bombay" -> "Mumbai") if
  the place is in `extraction.entities.HISTORICAL_PLACE_GAZETTEER`,
  otherwise the raw text, title-cased
- `date` — ISO 8601 (`YYYY-MM-DD`)
- `amount` — `"<CURRENCY> <amount>.<cents>"`, e.g. `"USD 128.50"` or
  `"GBP 5.21"` (GBP amounts are pounds + shillings/20 + pence/240, rounded
  to 2 decimals)

## Adding a new example

1. Add an image to `fixtures/images/`.
2. Transcribe its text exactly (case, punctuation) as one logical line —
   whitespace/line-break placement doesn't matter, only the words do.
3. List every entity a correct extraction should find, in the normalized
   form above.
4. Add an entry to `fixtures/ground_truth.json` with a unique `id`.
5. Run `python -m eval.report` and check the new example's row — a
   non-trivial CER on a clean image usually means either the transcription
   is wrong or the pipeline has a real bug worth investigating (check the
   "worst examples" section of the report for the actual predicted text).

## Current fixtures

The four checked-in fixtures are synthetic — rendered text (OpenCV
`putText`), not real archive scans — because no real scans were available
to hand-label yet. They're useful for catching regressions in the
OCR/extraction wiring itself (each one exercises a different entity-type
combination and currency format: decimal dollars, £/s/d ledger notation,
numeric-slash dates with a comma-separated dollar amount, and spelled-out
"N dollars and M cents"). They are *not* representative of real scan
quality (faded ink, skew, handwriting, scanner noise) — replace/extend them
with real hand-transcribed archive scans as those become available; that's
the set that will actually tell you whether a pipeline change helped.
