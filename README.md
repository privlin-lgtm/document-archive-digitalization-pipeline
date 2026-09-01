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
4. **Extract** (`extraction/`) — structured entities (names, dates, places,
   etc.) are pulled from the OCR text.
5. **Index** (`storage/`) — OCR text and entities are persisted to Postgres,
   with a full-text search index for querying the archive.
6. **Review** (`review_api/`, `web/`) — documents with low OCR/extraction
   confidence or detected anomalies are queued for a human reviewer via the
   annotation UI.

This scaffold wires up stages 1, 2, 3, and 6: upload → stored row → API is
end-to-end, and preprocessing/layout/OCR are implemented and tested. Entity
extraction (stage 4) and search indexing (stage 5) are still stubs; region
metadata from `layout.py` is structured to be persisted once the `regions`
table lands in stage 5, so the annotation UI can highlight "this date came
from this box on the page."

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

## Project layout

```
ingestion/     upload handling, storage backend writes
ocr/           OCR engine abstraction (multi-backend)
extraction/    structured entity extraction from OCR text
storage/       SQLAlchemy models, DB session, full-text index
review_api/    FastAPI app: health check, review/document endpoints
web/           annotation UI (served by review_api)
tests/         pytest suite
alembic/       DB migrations
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

Health check:

```bash
curl http://localhost:8000/health
```

## Configuration

All configuration is via environment variables (see `.env.example`):

- `DATABASE_URL` — Postgres connection string
- `OCR_ENGINE` — `tesseract` | `trocr` | `textract`
- `STORAGE_BACKEND` — `local` | `s3`, plus `STORAGE_LOCAL_PATH` /
  `STORAGE_S3_BUCKET` / `STORAGE_S3_REGION`
- `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` — Redis URLs for the async
  job queue
