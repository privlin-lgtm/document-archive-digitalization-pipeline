# Document Archive Pipeline

Ingests scanned images of handwritten and poorly-typed historical documents,
runs OCR, extracts structured entities, indexes the results in Postgres full-text
search, and surfaces low-confidence results for human review.

## Pipeline stages

1. **Upload** (`ingestion/`) — a scanned image is received via the API and
   written to the configured storage backend (local disk or S3); a `documents`
   row is created with status `uploaded`.
2. **Preprocess** (`ocr/preprocess.py`) — deskew, denoise, and binarize the
   raw scan so OCR gets a cleaner input, especially for faded/aged paper. Also
   computes a sharpness/contrast quality score to flag likely-poor-OCR scans
   early.
3. **OCR** (`ocr/engine.py`, `ocr/layout.py`) — before text extraction,
   `layout.py` segments the page into regions (paragraph, table, signature,
   margin annotation, stamp) via connected-component blobs + geometry
   heuristics, each with a bounding box and reading order. `engine.py` then
   routes each region to the matching OCR backend — Tesseract for typed
   text, a pluggable TrOCR/cloud backend for handwriting — falling back to
   the other backend if one fails or times out. Output is structured: full
   text, per-word bounding boxes, and document/line-level confidence.
4. **Extract** (`extraction/entities.py`) — a spaCy NER pipeline (base
   PERSON/GPE detection) plus two custom pipeline components layer
   rule-based parsing for historical dates ("the 3rd day of March, 1897",
   OCR-mangled digits like "l897") and currency (modern `$1,234.56` and
   pre-decimal `£3 12s 6d` ledger notation). Place names are disambiguated
   against a small historical-name gazetteer with rapidfuzz fuzzy matching,
   so a single OCR-misread character doesn't lose the match. Every entity
   carries a normalized value, raw text, a confidence blending the
   extraction method with OCR confidence, and an optional source region
   bbox.
5. **Index** (`storage/models.py`, `storage/queries.py`) — `documents` fans
   out to `pages` → `regions` → (`ocr_results`, `entities`), plus
   `review_flags`. `pages.full_text_search` is a stored generated tsvector
   column with a GIN index for full-text search (ranked via `ts_rank`,
   highlighted via `ts_headline`). `entities.date_value` uses a BRIN index
   instead of B-tree; `entities.raw_text`/`normalized_value` have pg_trgm
   GIN indexes for fuzzy "did you mean" search. `queries.py` has the
   example queries as parameterized functions, with a real EXPLAIN ANALYZE
   walkthrough (not a hypothetical one) of the BRIN/B-tree tradeoff in its
   module docstring.
6. **Review** (`extraction/anomalies.py`, `review_api/`, `web/`) — after OCR
   and extraction, `anomalies.py` flags content needing human review:
   `low_ocr_confidence` (a region or an individual word below threshold),
   `illegible` (near-zero confidence, or output that's mostly
   non-alphanumeric noise), `entity_conflict` (an implausible date/amount,
   or two person names within a document that are similar-but-not-identical
   — likely OCR variance on the same person), and `extraction_failure` (a
   table/signature region with no amount/person entity extracted from it).
   All thresholds are configurable (`config.py`), not hardcoded. Flags map
   onto `review_flags`, with a `status` (open/resolved/dismissed) for the
   review workflow.

This scaffold wires up stages 1-6: upload → stored row → API is
end-to-end, and preprocessing/layout/OCR/extraction/schema/anomaly-detection
are implemented and tested. The `regions`/`entities`/`review_flags` tables
are in place so the annotation UI can highlight "this date came from this
box on the page" — actually *writing* pipeline output into them (wiring
ingestion → OCR → extraction → anomaly detection → these tables end-to-end)
is stage 9's job.

### Trying the OCR engine

```bash
uv run python -m ocr.engine run path/to/scan.png
```

Prints structured JSON (text, per-word boxes/confidence, document/line
confidence). Requires the `tesseract-ocr` binary on PATH (installed
automatically in the Docker image) for the typed-text backend. The
handwriting backend (TrOCR) needs the optional `handwriting` extra:

```bash
uv sync --extra handwriting
```

### Trying entity extraction

```bash
uv run python -c "
from extraction.entities import extract_entities
for e in extract_entities('John Smith paid £3 12s 6d on the 3rd day of March, 1897 in Bombay.'):
    print(e)
"
```

Uses `en_core_web_sm` by default (installed automatically, no extra
download). For higher NER accuracy, install the transformer pipeline and
point `NER_MODEL` at it:

```bash
uv sync --extra ner-transformer
```
```
NER_MODEL=en_core_web_trf
```

### Trying the schema & example queries

```bash
docker compose up -d db
docker compose exec app uv run alembic upgrade head
docker compose exec -T db psql -U postgres -d document_archive -f - < scripts/seed_synthetic_data.sql
```

Seeds ~240k synthetic entities (in ~10s, via `generate_series` bulk SQL —
not an ORM loop) plus two hand-crafted documents with realistic
cross-referenced person/date/location/amount entities, for exercising
`storage/queries.py`'s example queries:

```bash
docker compose exec app uv run python -c "
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from storage import queries

engine = create_engine('postgresql+psycopg://postgres:postgres@db:5432/document_archive')
session = sessionmaker(bind=engine)()
for r in queries.full_text_search(session, 'John Smith Bombay'):
    print(r)
"
```

`tests/test_queries.py` runs against this seeded data when a live Postgres
is reachable (skipped otherwise, e.g. plain `uv run pytest` on the host —
the `db` service doesn't publish a host port, see Configuration below).

### Trying anomaly detection

```bash
uv run python -c "
from extraction.anomalies import detect_entity_conflicts
from extraction.entities import ExtractedEntity

entities = [
    ExtractedEntity('date', '2999-01-01', 'the 1st of January, 2999', 0.9, 0, 10),
    ExtractedEntity('person', 'John Smith', 'John Smith', 0.7, 0, 10),
    ExtractedEntity('person', 'John Smyth', 'John Smyth', 0.7, 20, 30),
]
for f in detect_entity_conflicts(entities):
    print(f)
"
```

Operates purely on the in-memory dataclasses from the OCR/extraction stages
(no DB needed) — `detect_all_anomalies` runs every check for one document's
regions/OCR results/entities at once.

## Project layout

```
ingestion/     upload handling, storage backend writes
ocr/           OCR engine abstraction (multi-backend)
extraction/    structured entity extraction from OCR text
storage/       SQLAlchemy models, DB session, full-text/BRIN/trigram search queries
review_api/    FastAPI app: health check, review/document endpoints
web/           annotation UI (served by review_api)
tests/         pytest suite
alembic/       DB migrations
scripts/       synthetic data seeding for exercising the schema at scale
config.py      pydantic-settings config, read from .env
worker.py      Celery app + task definitions for async OCR jobs
```

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) for dependency management
- Docker + Docker Compose for running the full stack

## Local development

```bash
cp .env.example .env
uv sync
uv run alembic upgrade head
uv run uvicorn review_api.main:app --reload
```

Run tests:

```bash
uv run pytest
```

## Running the full stack (Docker Compose)

```bash
cp .env.example .env
docker compose up --build
```

This starts Postgres, Redis, the FastAPI app (`app`), and a Celery worker
(`worker`) for async OCR jobs. Once the `db` service is healthy, run
migrations against it:

```bash
docker compose exec app uv run alembic upgrade head
```

Health check (open, no auth needed):

```bash
curl http://localhost:8000/health
```

`/documents*` requires a bearer token (see Configuration below):

```bash
curl -H "Authorization: Bearer $REVIEW_API_TOKEN" http://localhost:8000/documents
```

The `db` service no longer publishes its port to the host — `app`/`worker`
reach it over the internal compose network. For a local `psql` shell:

```bash
docker compose exec db psql -U postgres -d document_archive
```

## Configuration

All configuration is via environment variables (see `.env.example`). Two
have no default and the app fails fast at startup if they're unset:

- `DATABASE_URL` — Postgres connection string. The default in
  `.env.example` (`postgres`/`postgres`) is for local dev only — **rotate
  these credentials before any real deployment.**
- `REVIEW_API_TOKEN` — bearer token required on all `/documents*` routes
  (`/health` stays open for container healthchecks). Generate one with
  `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

Everything else has a sane default:

- `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_POOL_RECYCLE_SECONDS` — SQLAlchemy
  connection pool tuning
- `OCR_ENGINE` — `tesseract` | `trocr` | `textract`
- `STORAGE_BACKEND` — `local` | `s3`, plus `STORAGE_LOCAL_PATH` /
  `STORAGE_S3_BUCKET` / `STORAGE_S3_REGION`
- `MAX_UPLOAD_SIZE_BYTES` — upload size cap enforced by `ingestion.upload`
- `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` — Redis URLs for the async
  job queue

`GET /documents` is paginated (`limit`, default 50, max 500; `offset`) —
it does not return the whole table in one response.

## Known limitations / accepted follow-up work

- `ocr/layout.py`'s region classification thresholds are tuned against
  synthetic test fixtures, not validated against real scans — expect
  misclassification on real documents until calibrated (planned for the
  stage 6 review-flagging work).
- `OCRRouter`'s per-backend timeout can't forcibly kill a hung thread
  (a Python limitation) — on timeout it stops waiting and falls back
  immediately, but the original call keeps running in the background until
  it finishes on its own. A true kill needs a process-based executor.
- `extraction/entities.py`'s location detection depends on spaCy's base NER
  first finding *some* entity span — `en_core_web_sm` was trained mostly on
  modern text, so it sometimes mislabels historical/colonial place names
  (e.g. "Calcutta" as PRODUCT) or misses them entirely. Two bounded
  fallbacks recover common cases (checking ORG/PRODUCT-labeled spans and
  untagged proper nouns against the gazetteer), but only ever on an exact or
  fuzzy gazetteer hit — a place with no gazetteer entry that the base model
  also fails to tag will be missed. `en_core_web_trf` (the `ner-transformer`
  extra) would improve base coverage.
