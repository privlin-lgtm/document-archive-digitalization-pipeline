# Document Archive Pipeline

Ingests scanned images of handwritten and poorly-typed historical documents,
runs OCR, extracts structured entities, indexes the results in Postgres full-text
search, and surfaces low-confidence results for human review.

## Pipeline stages

1. **Upload** (`ingestion/`) — a scanned image is received via the API and
   written to the configured storage backend (local disk or S3); a `documents`
   row is created with status `uploaded`.
2. **Preprocess** — deskew, denoise, and binarize the raw scan so OCR gets a
   cleaner input, especially for faded/aged paper.
3. **OCR** (`ocr/`) — an async worker job runs the configured OCR engine
   (Tesseract, TrOCR, or Textract) against the preprocessed image.
4. **Extract** (`extraction/`) — structured entities (names, dates, places,
   etc.) are pulled from the OCR text.
5. **Index** (`storage/`) — OCR text and entities are persisted to Postgres,
   with a full-text search index for querying the archive.
6. **Review** (`review_api/`, `web/`) — documents with low OCR/extraction
   confidence or detected anomalies are queued for a human reviewer via the
   annotation UI.

This scaffold wires up stages 1 and 6 end-to-end (upload → stored row → API)
with stubs for stages 2-5; OCR and extraction logic are implemented in later
iterations.

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
