"""Pipeline smoke test: ingests a handful of sample scans, runs the full
pipeline (pipeline.run.run_pipeline, called synchronously -- no Celery/Redis
needed) end-to-end for each, and asserts every document reaches a terminal
status with entities and review flags actually populated.

This is genuinely end-to-end: sample images are real rendered text (same
technique as tests/test_ocr_engine.py's make_typed_image), run through real
preprocessing, layout detection, Tesseract OCR, entity extraction, and
anomaly detection -- nothing here is mocked. It needs an actual `tesseract`
binary on PATH (the project Docker image installs it; a bare host venv may
not have it -- see the repo README) and a real Postgres reachable via
DATABASE_URL (pages.full_text_search is a generated tsvector column that
doesn't exist on SQLite).

Usage:
    docker compose up -d db redis
    docker compose run --rm app alembic upgrade head
    docker compose run --rm app python scripts/smoke_test.py
"""

from __future__ import annotations

import logging
import sys
import uuid
from pathlib import Path

import cv2
import numpy as np
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingestion.upload import save_raw_image
from pipeline.run import run_pipeline
from storage.db import SessionLocal
from storage.models import Document, DocumentStatus, Entity, Page, Region, ReviewFlag

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {DocumentStatus.ready, DocumentStatus.needs_review}


def _text_page(lines: list[str], width: int = 1400, height: int = 900) -> np.ndarray:
    page = np.full((height, width), 255, dtype=np.uint8)
    y = 150
    for line in lines:
        cv2.putText(page, line, (80, y), cv2.FONT_HERSHEY_SIMPLEX, 1.1, 0, 2, cv2.LINE_AA)
        y += 100
    return page


def _noise_page(width: int = 1400, height: int = 900, seed: int = 7) -> np.ndarray:
    """No real content -- simulates a badly degraded/illegible scan."""
    rng = np.random.default_rng(seed)
    return rng.integers(90, 170, size=(height, width), dtype=np.uint8)


# Each sample document, built and expected to trip a specific part of the
# pipeline: a normal clean scan, one with an anomalous entity, and one
# that's effectively unreadable.
SAMPLE_IMAGES: dict[str, np.ndarray] = {
    "clean.png": _text_page(
        [
            "Report filed by John A. Smith",
            "Location: Bombay, dated the 3rd day of March, 1897",
            "Amount received: $128.50",
        ]
    ),
    "implausible_amount.png": _text_page(["Amount paid to the estate: $50,000,000.00"]),
    "low_quality.png": _noise_page(),
}


def _ingest(name: str, image: np.ndarray) -> uuid.UUID:
    ok, buffer = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError(f"failed to encode sample image {name}")
    document_id = uuid.uuid4()
    raw_image_path = save_raw_image(document_id, name, buffer.tobytes())

    session = SessionLocal()
    try:
        session.add(
            Document(id=document_id, filename=name, raw_image_path=raw_image_path, status=DocumentStatus.uploaded)
        )
        session.commit()
    finally:
        session.close()
    return document_id


def _document_summary(document_id: uuid.UUID) -> dict:
    session = SessionLocal()
    try:
        document = session.get(Document, document_id)
        entity_count = session.scalar(
            select(func.count())
            .select_from(Entity)
            .join(Region, Region.id == Entity.region_id)
            .join(Page, Page.id == Region.page_id)
            .where(Page.document_id == document_id)
        )
        flag_count = session.scalar(
            select(func.count()).select_from(ReviewFlag).where(ReviewFlag.document_id == document_id)
        )
        return {"status": document.status, "entity_count": entity_count or 0, "flag_count": flag_count or 0}
    finally:
        session.close()


def run_smoke_test() -> bool:
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger.info("running pipeline smoke test against %d sample document(s)", len(SAMPLE_IMAGES))

    summaries: dict[str, dict] = {}
    for name, image in SAMPLE_IMAGES.items():
        document_id = _ingest(name, image)
        run_pipeline(str(document_id))

        summaries[name] = _document_summary(document_id)
        s = summaries[name]
        logger.info(
            "document=%s status=%s entities=%d flags=%d",
            name, s["status"].value, s["entity_count"], s["flag_count"],
        )

    ok = True
    for name, s in summaries.items():
        if s["status"] not in _TERMINAL_STATUSES:
            logger.error("FAIL: %s did not reach a terminal status (got %s)", name, s["status"].value)
            ok = False

    if summaries["clean.png"]["entity_count"] == 0:
        logger.error("FAIL: clean.png produced no entities")
        ok = False

    total_flags = sum(s["flag_count"] for s in summaries.values())
    if total_flags == 0:
        logger.error(
            "FAIL: no review flags were raised across the batch (expected the implausible-amount "
            "and low-quality samples to trip anomaly checks)"
        )
        ok = False

    if ok:
        logger.info("PASS: all documents reached a terminal status; entities and flags populated as expected")
    return ok


if __name__ == "__main__":
    sys.exit(0 if run_smoke_test() else 1)
