# Prompt Series: Historical Document Archive Pipeline

A staged set of prompts for building the MVP with an AI coding assistant. Each
prompt is tagged with which tool fits it better — **Claude Code** or **Cursor**
— and why. Use them in order; each stage assumes the previous one's output
exists in the repo.

## When to use which (decision framework)

These two tools overlap a lot, so treat this as a default, not a rule. Switch
if your own workflow preference differs.

| Use **Claude Code** when... | Use **Cursor** when... |
|---|---|
| The task is well-specified and spans many new files (scaffolding, a new module, a migration) | You're iterating on something visual and want to see it render as you go |
| You want to hand off a chunk of work, walk away, and review a diff/PR when it's done | You want to stay in the loop, editing line-by-line alongside the AI |
| The work involves running things — tests, migrations, docker, CLI tools — and iterating on failures autonomously | You're making small, exploratory edits inside a file you're actively reading |
| It's backend/data/infra logic where correctness matters more than "feel" | It's frontend/UI/styling work where the right answer is "does it look and feel right" |
| You're doing a large multi-file refactor across the whole repo | You're pairing on a single function or debugging one specific spot |

Rule of thumb: **Claude Code for breadth and autonomy, Cursor for depth and
feel.** Backend/pipeline/infra stages below lean Claude Code; the UI stage
leans Cursor; a couple are genuinely hybrid.

---

## Stage 0 — Project Scaffolding & Architecture

**Recommended: Claude Code.** This is a from-scratch, multi-file scaffold
(folders, docker-compose, Alembic, config) with no visual component — exactly
the "give it a spec, review the resulting structure" shape Claude Code
handles well, including running `docker compose up` and the initial migration
to confirm it works.

```
Set up a Python project for a document-archive pipeline that ingests scanned
images of handwritten/poorly typed historical documents, runs OCR, extracts
structured entities, stores results in Postgres with full-text search, and
surfaces anomalies for human review.

Requirements:
- Python 3.11+, managed with uv or poetry
- FastAPI backend, Postgres 15+ (via SQLAlchemy + Alembic migrations)
- Folder structure separating: ingestion/, ocr/, extraction/, storage/, review_api/,
  web/ (annotation UI), tests/
- A docker-compose.yml with services for the app, Postgres, and a background worker
  (Celery or RQ) for async OCR jobs
- .env-based config (DB url, OCR engine choice, storage backend path/S3)
- A README describing the pipeline stages: upload -> preprocess -> OCR -> extract ->
  index -> review

Produce the initial scaffold, a health-check endpoint, and a stub Alembic migration
for a `documents` table with id, filename, upload_time, status, raw_image_path.
Don't implement OCR or extraction logic yet — just the skeleton and wiring.
Run the stack and confirm docker compose starts and the health check responds.
```

---

## Stage 1 — Image Preprocessing

**Recommended: Claude Code.** Algorithmic image-processing code with unit
tests against synthetic degraded images — success is measured by tests
passing, not by eyeballing output, so there's little benefit to Cursor's
tight visual loop here. (If you later want to visually tune thresholds
against real scans, that specific tuning pass is a good candidate to drop
into Cursor.)

```
Implement an image preprocessing module (ocr/preprocess.py) that prepares scanned
document images for OCR. Use OpenCV and Pillow.

Requirements:
- Deskew rotated scans (Hough transform or projection profile method)
- Denoise (bilateral filter or non-local means) without destroying faint handwriting
- Adaptive thresholding / binarization tuned for aged, low-contrast paper
- Auto-crop to remove scanner borders/black edges
- Optional: contrast-limited adaptive histogram equalization (CLAHE) as a fallback
  for very faded documents
- Return both the processed image and a quality score (e.g., estimated blur/contrast
  metric) so downstream stages can flag "likely poor OCR" documents early

Write it as a pipeline of composable steps (each a function taking/returning a
numpy array) so individual steps can be toggled per document type. Include unit
tests using synthetic degraded images (rotated, noisy, low-contrast) generated
in the test itself. Run the test suite and iterate until it passes.
```

---

## Stage 2 — OCR Engine Wrapper (Handwriting + Poor Typescript)

**Recommended: Claude Code.** Multi-backend integration with mocked tests
and a CLI entry point — autonomous, testable, no UI. Good candidate to run
unattended and review the diff.

```
Build an OCR abstraction layer (ocr/engine.py) that can run multiple OCR backends
and pick/blend results, since no single engine handles both handwriting and old
typewriter fonts well.

Requirements:
- Support at least two backends behind a common interface:
  1. Tesseract (via pytesseract) for typed text, tuned with custom config for
     historical fonts (--psm modes, language packs)
  2. A transformer-based handwriting model (e.g., TrOCR via HuggingFace
     transformers, or a cloud OCR API as a pluggable alternative) for cursive/
     handwritten sections
- A per-document (or per-region) router: use a lightweight heuristic or classifier
  to decide whether a page/region looks handwritten vs typed, and dispatch to the
  right engine
- Return OCR output as a structured result: full text, per-word bounding boxes,
  and per-word confidence scores — not just a flat string
- A confidence aggregation step that computes a document-level and line-level
  confidence score
- Graceful degradation: if a backend fails or times out, fall back to the other
  and mark the document as `ocr_partial`

Include a CLI command (`python -m ocr.engine run <path>`) for testing on a single
file, and unit tests with mocked backend responses. Run it against a sample image
and confirm structured output looks correct.
```

---

## Stage 3 — Layout & Region Segmentation

**Recommended: Claude Code.** Same reasoning as Stage 1 — heuristic/algorithmic
logic validated by tests on synthetic layouts, no visual iteration needed yet.

```
Add a layout segmentation step (ocr/layout.py) that runs before OCR text extraction
to split a scanned page into regions (paragraphs, tables, signatures, margins/
annotations, stamps) so OCR and entity extraction can be applied per-region rather
than to the whole page as one blob.

Requirements:
- Use a layout detection approach appropriate for the project (e.g., a
  connected-component + heuristic approach for MVP, with a clear extension point
  to swap in a learned layout model like LayoutParser/Detectron2 later)
- Output a list of regions per page with bounding box, region type, and reading
  order
- Handle common archive-document patterns: ledger tables (rows/columns of names +
  amounts), letters (header/body/signature), forms (label/value pairs)
- Persist region metadata alongside OCR text so the annotation UI can later
  highlight "this date came from this box on the page"

Write tests using a few synthetic mock layouts (rectangles drawn programmatically)
verifying regions are detected and ordered top-to-bottom, left-to-right.
```

---

## Stage 4 — Structured Entity Extraction

**Recommended: Claude Code.** Rule-based/NLP logic plus a substantial test
suite (10+ cases per entity type) — well-specified, testable, autonomous.

```
Implement an entity extraction module (extraction/entities.py) that pulls
structured data out of noisy OCR text: person names, dates, locations, and cash
amounts.

Requirements:
- Use spaCy (with a transformer pipeline, e.g., en_core_web_trf) as the base NER
  engine, plus custom rule-based/regex components layered on top for:
  - Historical date formats (e.g., "the 3rd day of March, 1897", abbreviated
    months, OCR-mangled digits like "l8" for "18")
  - Currency amounts including pre-decimal/historical formats (£/s/d, "dollars
    and cents" spelled out, handwritten fractions)
  - Place names, with a gazetteer-based disambiguation step for historical place
    names that may not match modern geocoding
- Each extracted entity must carry: entity type, normalized value (e.g., ISO date
  where derivable), raw source text, confidence, and the source region/bounding
  box it came from
- Build in OCR-error tolerance: fuzzy matching (e.g., rapidfuzz) so that entities
  aren't lost purely because of a single misread character
- A confidence score per entity that combines OCR confidence for that text span
  with NER model confidence

Write a spaCy custom pipeline component for the date/currency rules, and unit
tests covering at least 10 realistic messy-OCR input strings per entity type
(names, dates, locations, amounts), asserting correct normalized extraction.
```

---

## Stage 5 — Postgres Schema & Search Indexing

**Recommended: Claude Code.** This stage needs to actually run migrations,
insert synthetic data at scale, and run `EXPLAIN ANALYZE` to justify the
BRIN-vs-B-tree choice — that's a "run commands, inspect output, iterate"
loop Claude Code is built for, not something you eyeball in an editor.

```
Design and implement the Postgres schema (via SQLAlchemy models + Alembic
migration) for storing documents, OCR output, and extracted entities, optimized
for full-text search and time/location range queries at archive scale
(hundreds of thousands of pages).

Requirements:
- Tables: documents, pages, regions, ocr_results, entities (typed: person, date,
  location, amount), review_flags
- Full-text search: add a `tsvector` column on the searchable text (page or
  document level), generated via a trigger or GENERATED ALWAYS AS, with a GIN
  index; support ranked search (ts_rank) and highlighting (ts_headline)
- For entities.date_value (a date/date-range column) and any large sequentially-
  inserted timestamp columns, use a BRIN index instead of B-tree — explain in a
  comment why BRIN fits (large table, naturally correlated with insertion order,
  much smaller index footprint than B-tree)
- Add trigram (pg_trgm) indexes on entity name fields to support fuzzy "did you
  mean" search against OCR-garbled names
- Write example queries: full-text search across all documents with snippet
  highlighting, "all cash amounts over $X between two dates", "documents
  mentioning person X near location Y"
- Include an EXPLAIN ANALYZE walkthrough (as a doc comment) showing the BRIN vs
  B-tree tradeoff on the date column for a large synthetic dataset

Produce the SQLAlchemy models, the Alembic migration, and a queries.py module
with the example queries as parameterized functions. Actually run the migration
against a local Postgres instance, seed synthetic data, and paste real
EXPLAIN ANALYZE output into the comment rather than a hypothetical one.
```

---

## Stage 6 — Anomaly & Low-Confidence Detection

**Recommended: Claude Code.** Rule-driven module with unit tests per flag
type — no visual component, straightforward autonomous task.

```
Build a review-flagging module (extraction/anomalies.py) that runs after OCR and
entity extraction to automatically flag content that needs human review.

Requirements:
- Flag types:
  - `low_ocr_confidence`: any region/word below a configurable confidence
    threshold
  - `illegible`: regions where OCR confidence is near-zero or output is mostly
    non-alphanumeric noise
  - `entity_conflict`: e.g., a date outside a plausible range for the archive,
    a cash amount with implausible magnitude, inconsistent name spellings for
    what looks like the same person across a document (fuzzy-match near-
    duplicates)
  - `extraction_failure`: expected entity type not found where layout suggests
    one should be (e.g., a ledger row with no amount detected)
- Each flag stores: type, severity, the source region/entity reference, and a
  human-readable explanation string (e.g., "OCR confidence 32%, below threshold
  of 60%")
- Make thresholds configurable via settings, not hardcoded
- Emit flags as rows in the `review_flags` table tied to document/page/entity,
  with a `status` field (open/resolved/dismissed) for the review workflow

Write unit tests covering each flag type with constructed low-confidence/
conflicting input data.
```

---

## Stage 7 — Review & Search API

**Recommended: Claude Code.** Endpoint implementation plus integration tests
against a real/test database — another "run it, hit it with requests, fix
failures" loop that benefits from autonomy over hand-holding.

```
Build the FastAPI endpoints (review_api/) that the annotation UI will consume.

Requirements:
- POST /documents — upload a scan (or batch), enqueue the async processing job
- GET /documents/{id} — status, OCR text, regions, entities, flags
- GET /documents/{id}/image — serve the (optionally region-annotated) image
- GET /search — full-text search with filters for date range, entity type,
  location, min confidence; returns ranked results with highlighted snippets
- PATCH /entities/{id} — human correction of an extracted entity value
  (records original + corrected value + reviewer + timestamp, doesn't destroy
  the original OCR output)
- PATCH /review_flags/{id} — resolve/dismiss a flag
- GET /stats — dashboard summary: documents processed, pending review count,
  average confidence, flags by type

Use Pydantic schemas for all request/response bodies, paginate list endpoints,
and add OpenAPI descriptions. Include integration tests using a test database
(pytest + a Postgres test container or SQLite fallback where feasible for CI).
Run the test suite and confirm everything passes.
```

---

## Stage 8 — Document Annotation UI

**Recommended: Cursor (start with Claude Code, then switch).** This is the
one stage where the tool choice really matters. Have Claude Code generate the
initial scaffold (component structure, API client, routing) in one shot —
that part is boilerplate and benefits from speed. Then move into Cursor for
the actual annotation experience: syncing bounding-box overlays with the
entity panel, tuning confidence color-coding, getting click-to-highlight
feeling responsive, keyboard shortcuts. That's inherently a "look at the
screen, nudge it, look again" loop, which is what Cursor's inline/live-preview
workflow is built for. Trying to get pixel- and interaction-level UI feel
right through an autonomous agent you're not watching in real time tends to
take more round-trips than doing it interactively.

```
Build a clean, focused annotation/review web UI (React + TypeScript) for human
reviewers, consuming the FastAPI endpoints above.

Requirements:
- Split-pane document view: scanned image on one side (pan/zoom, with bounding
  box overlays for detected regions and entities) and structured extraction
  results on the other, kept in sync — clicking an entity highlights its region
  on the image and vice versa
- Color-code overlays by confidence (e.g., red/amber/green) and by flag type
- A correction workflow: click an entity, edit its value inline, submit —
  calls PATCH /entities/{id}; visually distinguish "AI-extracted" vs
  "human-corrected" entities
- A review queue view: list of flagged documents/entities sorted by severity,
  with quick resolve/dismiss actions
- A search view: full-text search bar with filters (date range, entity type,
  location), results list with highlighted snippets, click-through to the
  document view
- Keep it fast for reviewers processing many documents: keyboard shortcuts for
  next/prev flag, approve, and skip

Use a component library appropriate for dense data UIs, and structure state
management so the image/entity sync doesn't require prop-drilling through many
layers. Don't worry about auth/multi-user roles yet — assume a single reviewer
role for MVP.
```

---

## Stage 9 — End-to-End Pipeline Wiring & Batch Ingestion

**Recommended: Claude Code.** Orchestration across existing modules, running
Celery/RQ, running a smoke test end-to-end, and iterating on failures — an
autonomous, run-and-verify task with no UI component.

```
Wire all stages into a single async pipeline (Celery/RQ task) that runs on
document upload: preprocess -> layout segmentation -> OCR -> entity extraction
-> anomaly flagging -> index for search -> mark document status `ready_for_review`
or `ready` if no flags.

Requirements:
- Idempotent, resumable per-stage (store intermediate state so a failure at
  extraction doesn't force re-running OCR)
- Structured logging per stage with timing, so slow stages on large batches are
  identifiable
- A batch ingestion CLI/script for archives dropping in hundreds of scans at
  once (e.g., a folder watcher or a `python -m ingestion.batch <dir>` command),
  with concurrency limits to avoid overwhelming the OCR backend
- Basic retry/backoff for transient OCR backend failures
- A smoke-test script that ingests a handful of sample images end-to-end and
  asserts documents reach a terminal status with entities and flags populated

Also produce a short architecture diagram (Mermaid) showing the pipeline stages
and data flow into Postgres, for the README. Run the smoke test and confirm it
passes before finishing.
```

---

## Stage 10 — Evaluation Harness (Optional but Recommended)

**Recommended: Claude Code.** Standalone script + metrics computation +
report generation, run against fixtures — no visual/interactive component.

```
Build a lightweight evaluation harness (eval/) to measure OCR and extraction
quality against a small hand-labeled ground-truth set, so pipeline changes
(e.g., swapping OCR backends, tuning thresholds) can be judged objectively.

Requirements:
- A ground-truth format (JSON/CSV) pairing a sample scan with its correct text
  and correct entities
- Metrics: character error rate (CER) and word error rate (WER) for OCR text;
  precision/recall/F1 per entity type for extraction
- A report generator that runs the current pipeline against the ground-truth
  set and outputs a summary table plus worst-performing examples for
  inspection
- Document how to add new ground-truth examples as the archive's document
  types diversify

Keep this decoupled from production code paths — it should be runnable as a
standalone script against a checked-in `eval/fixtures/` directory. Run it
against a small fixture set and paste the resulting report into the PR
description or commit message.
```

---

### Suggested order of execution
Stages 0–2 give you a working OCR path end to end on a single document. Stages
3–4 add structure. Stage 5 makes it searchable at scale. Stage 6–7 add the
review/API layer. **Stage 8 is your one deliberate tool switch** — scaffold
with Claude Code, then finish in Cursor. Stage 9 ties it together for real
archive-scale ingestion. Stage 10 is worth adding once the pipeline is stable
enough that you want to detect regressions.

### General tip
If you find yourself fighting Cursor's tight loop on something that's really
a big autonomous refactor, or fighting Claude Code's "review the diff after"
model on something that needs constant visual judgment call after judgment
call, that's a sign you're on the wrong tool for that particular task —
switch rather than push through.
