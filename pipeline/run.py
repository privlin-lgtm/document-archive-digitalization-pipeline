"""End-to-end document pipeline: preprocess -> layout segmentation -> OCR ->
entity extraction -> anomaly flagging -> index for search -> final status.

Each stage checks persisted state before doing work, so a retry after a
mid-pipeline crash resumes rather than restarting from scratch — the
expensive stage this protects is OCR (one call per region, potentially a
heavy transformer model for handwriting); preprocessing/layout are cheap,
deterministic functions of the raw image and are simply redone on resume.
Entity extraction and anomaly detection are also cheap/deterministic, so
their "resumability" is just "skip if already persisted" without needing any
extra state — rerunning a region that legitimately produced zero entities is
harmless (it produces zero entities again, no duplicates).

This module works one page per document (page_number=1) — nothing upstream
(ingestion.upload, this module) splits a multi-page source (e.g. a
multi-page TIFF/PDF) into separate pages yet; that's a real gap for a future
stage, not something this pipeline wiring invents a design for.

See worker.run_ocr_job for the Celery entry point (job-level retry/backoff
for transient infrastructure failures) and pipeline.smoke_test-style usage
in scripts/smoke_test.py for an end-to-end example.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING
from uuid import UUID

import cv2
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from extraction.anomalies import detect_all_anomalies
from extraction.entities import ExtractedEntity, extract_entities
from ocr.engine import OCRResult, OCRRouter, TesseractBackend, TrOCRBackend
from ocr.layout import BBox, detect_regions
from ocr.layout import Region as LayoutRegion
from ocr.pages import load_page_images
from ocr.preprocess import preprocess
from review_api.metrics import inc as inc_metric
from storage.db import SessionLocal
from storage.models import (
    Document,
    DocumentStatus,
    Entity,
    EntityType,
    FlagSeverity,
    FlagType,
    OCRResultRecord,
    OCRStatus,
    Page,
    RegionType,
    ReviewFlag,
)
from storage.models import Region as DBRegion
from storage.paths import processed_image_dest
from storage.typed_values import parse_amount_from_entity, parse_date_from_entity

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)

# Retry/backoff for a transient OCR-backend failure (both backends failed on
# a single call) -- distinct from OCRRouter's own primary/fallback logic,
# which handles one backend being *unavailable*, not a call that would
# likely succeed if simply retried (e.g. a momentary resource hiccup).
OCR_MAX_ATTEMPTS = 3
OCR_RETRY_BACKOFF_SECONDS = 2.0

_router: OCRRouter | None = None


def _handwriting_backend_available() -> bool:
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except ImportError:
        return False
    return True


def _get_router() -> OCRRouter:
    """Lazily built, module-level: TrOCRBackend defers loading its model
    weights until first use, so constructing it here is cheap, and reusing
    one router avoids reloading the (optional, heavy) handwriting model on
    every call.

    OCRRouter already logs a warning *per call* when it falls back from an
    unavailable primary backend (see ocr.engine), but that's easy to miss
    in a busy log stream and says nothing about *why* it's unavailable. If
    the `handwriting` extra (torch/transformers) isn't installed -- which
    it isn't by default; it's a large optional dependency -- every
    handwritten region in every document this worker ever processes
    silently downgrades to the typed-text backend for the process's entire
    lifetime. That's worth one loud, explicit warning at startup, not
    something an operator has to notice by pattern-matching per-call logs.
    """
    global _router
    if _router is None:
        if not _handwriting_backend_available():
            logger.warning(
                "handwriting OCR is disabled for this worker process: the 'torch'/"
                "'transformers' packages (the 'handwriting' extra) aren't installed. "
                "Every region classified as handwritten will fall back to the typed-text "
                "backend (Tesseract) instead. If this archive contains handwritten "
                "material, install with `uv sync --extra handwriting` -- see README.md."
            )
        _router = OCRRouter(typed_backend=TesseractBackend(), handwriting_backend=TrOCRBackend())
    return _router


@contextmanager
def _stage_timer(document_id: str, stage: str):
    start = time.perf_counter()
    try:
        yield
    finally:
        logger.info(
            "pipeline_stage document_id=%s stage=%s duration_s=%.3f",
            document_id, stage, time.perf_counter() - start,
        )


def _run_ocr_with_retry(image: np.ndarray) -> OCRResult:
    router = _get_router()
    result: OCRResult | None = None
    for attempt in range(1, OCR_MAX_ATTEMPTS + 1):
        result = router.run(image)
        if result.status != "failed":
            return result
        if attempt < OCR_MAX_ATTEMPTS:
            delay = OCR_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "ocr attempt %d/%d failed (%s), retrying in %.1fs",
                attempt, OCR_MAX_ATTEMPTS, "; ".join(result.notes), delay,
            )
            time.sleep(delay)
    return result  # type: ignore[return-value]  # loop always runs >=1 time


class DocumentLockTimeout(Exception):
    """Could not acquire a document's advisory lock within the wait bound."""


# pg_advisory_lock() blocks indefinitely -- if a redelivered duplicate job
# (see _ensure_page_and_regions's docstring on why redelivery races happen)
# lands behind a first attempt that's stuck or simply slow, the second
# worker would otherwise sit parked on this call for up to the task's own
# time limit (worker.py's task_time_limit=900s), tying up a concurrency
# slot for nothing useful. Poll with pg_try_advisory_lock and give up with a
# bounded wait instead, so a stuck sibling fails fast and lets Celery's own
# job-level retry/backoff (worker.run_ocr_job) handle it like any other
# transient failure.
_ADVISORY_LOCK_MAX_WAIT_SECONDS = 60.0
_ADVISORY_LOCK_POLL_SECONDS = 1.0


def _advisory_lock_key(document_id: UUID) -> int:
    return int(document_id.int % (2**63 - 1))


def _advisory_lock(session, document_id: UUID) -> None:
    if session.get_bind().dialect.name != "postgresql":
        return
    key = _advisory_lock_key(document_id)
    waited = 0.0
    while True:
        acquired = session.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar()
        if acquired:
            return
        if waited >= _ADVISORY_LOCK_MAX_WAIT_SECONDS:
            raise DocumentLockTimeout(
                f"could not acquire pipeline lock for document {document_id} "
                f"within {_ADVISORY_LOCK_MAX_WAIT_SECONDS}s (another run is still in progress)"
            )
        time.sleep(_ADVISORY_LOCK_POLL_SECONDS)
        waited += _ADVISORY_LOCK_POLL_SECONDS


def _advisory_unlock(session, document_id: UUID) -> None:
    if session.get_bind().dialect.name != "postgresql":
        return
    session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _advisory_lock_key(document_id)})


def _load_page_and_regions(
    session, document: Document, page_number: int = 1
) -> tuple[Page, list[DBRegion]] | None:
    page = session.scalar(
        select(Page).where(Page.document_id == document.id, Page.page_number == page_number)
    )
    if page is None:
        return None
    db_regions = list(
        session.scalars(
            select(DBRegion).where(DBRegion.page_id == page.id).order_by(DBRegion.reading_order)
        ).all()
    )
    return page, db_regions


def _wait_for_regions(session, document: Document, page_number: int) -> tuple[Page, list[DBRegion]] | None:
    """A racing winner may have committed the page row before inserting regions."""
    existing = _load_page_and_regions(session, document, page_number)
    if existing is None:
        return None
    page, db_regions = existing
    if db_regions:
        return existing
    for _ in range(3):
        time.sleep(0.05)
        session.expire_all()
        existing = _load_page_and_regions(session, document, page_number)
        if existing is None:
            return None
        page, db_regions = existing
        if db_regions:
            return existing
    return page, db_regions


def _persist_processed_image(
    session, document: Document, page: Page, image: np.ndarray, page_number: int
) -> None:
    dest = processed_image_dest(document.id, page_number)
    if not cv2.imwrite(str(dest), image):
        raise ValueError(f"failed to write processed image to {dest}")
    page.processed_image_path = str(dest)
    if page_number == 1:
        document.processed_image_path = str(dest)
    session.commit()


def _flag_empty_layout(session, document: Document, page: Page) -> None:
    existing = session.scalar(
        select(func.count())
        .select_from(ReviewFlag)
        .where(
            ReviewFlag.document_id == document.id,
            ReviewFlag.page_id == page.id,
            ReviewFlag.flag_type == FlagType.extraction_failure,
        )
    )
    if existing:
        return
    session.add(
        ReviewFlag(
            document_id=document.id,
            page_id=page.id,
            flag_type=FlagType.extraction_failure,
            severity=FlagSeverity.high,
            explanation="No layout regions were detected on this page; OCR was skipped.",
        )
    )
    session.commit()


def _ensure_page_and_regions(
    session, document: Document, image: np.ndarray, page_number: int = 1
) -> tuple[Page, list[DBRegion], np.ndarray]:
    """Load this document's single page/regions if the pipeline already got
    this far on a prior attempt, otherwise preprocess + detect + persist
    them now. Bbox coordinates are only valid against the *preprocessed*
    image (deskew/auto_crop change dimensions), so callers must crop from
    the returned image, never the raw one.

    Two pipeline runs for the *same* document can race here -- Celery's
    default delivery is at-least-once, so a broker redelivery (e.g. after a
    visibility-timeout hiccup) can start a second run_pipeline() call while
    the first is still in flight. Both would see no existing page from the
    SELECT above and both attempt to create one; `pages` has a unique
    (document_id, page_number) constraint, so the loser's flush raises
    IntegrityError instead of silently duplicating data. That's caught
    below and treated as "someone else already did this" rather than a
    fatal error.
    """
    preprocessed = preprocess(image)

    existing = _wait_for_regions(session, document, page_number)
    if existing is not None:
        page, db_regions = existing
        return page, db_regions, preprocessed.image

    layout_regions = detect_regions(preprocessed.image)
    page = Page(document_id=document.id, page_number=page_number)
    session.add(page)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        existing = _wait_for_regions(session, document, page_number)
        if existing is None:
            raise  # not actually a duplicate-page race -- some other integrity error
        page, db_regions = existing
        logger.info(
            "document_id=%s lost a concurrent page-creation race; reusing the winner's page %s",
            document.id, page.id,
        )
        return page, db_regions, preprocessed.image

    db_regions = []
    for lr in layout_regions:
        x, y, w, h = lr.bbox
        region = DBRegion(
            page_id=page.id,
            bbox_x=x, bbox_y=y, bbox_w=w, bbox_h=h,
            region_type=RegionType(lr.region_type),
            reading_order=lr.reading_order,
            confidence=lr.confidence,
        )
        session.add(region)
        db_regions.append(region)
    session.commit()

    logger.info(
        "document_id=%s preprocess quality_score=%.3f region_count=%d",
        document.id, preprocessed.quality_score, len(db_regions),
    )
    return page, db_regions, preprocessed.image


def _run_ocr_stage(
    session, db_regions: list[DBRegion], image: np.ndarray
) -> tuple[bool, dict[int, OCRResult]]:
    """Runs OCR for every region without a persisted result yet. Returns
    (any_region_not_fully_ok, {region_index: OCRResult}) -- the dict covers
    *every* region (freshly run or already-persisted), so downstream stages
    have a uniform view regardless of what this call actually did. A region
    reloaded from a prior run has no persisted per-word confidences (the
    schema only stores the aggregate), so its OCRResult.words is empty; that
    only costs the word-level variant of detect_low_ocr_confidence for a
    resumed region, not the region-level checks, which only need the
    aggregate confidence.
    """
    any_partial = False
    ocr_results: dict[int, OCRResult] = {}

    for idx, region in enumerate(db_regions):
        existing = session.scalar(
            select(OCRResultRecord).where(OCRResultRecord.region_id == region.id)
        )
        if existing is not None:
            ocr_results[idx] = OCRResult(
                text=existing.text,
                words=[],
                engine=existing.engine,
                document_confidence=existing.confidence,
                line_confidences={},
                status=existing.status.value,
                notes=list(existing.notes or []),
            )
            if existing.status != OCRStatus.ok:
                any_partial = True
            continue

        x, y, w, h = region.bbox_x, region.bbox_y, region.bbox_w, region.bbox_h
        crop = image[y : y + h, x : x + w]
        result = _run_ocr_with_retry(crop)

        record = OCRResultRecord(
            region_id=region.id,
            engine=result.engine,
            text=result.text,
            confidence=result.document_confidence,
            status=OCRStatus(result.status),
            notes=result.notes or None,
        )
        session.add(record)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            existing = session.scalar(
                select(OCRResultRecord).where(OCRResultRecord.region_id == region.id)
            )
            if existing is None:
                raise
            result = OCRResult(
                text=existing.text,
                words=[],
                engine=existing.engine,
                document_confidence=existing.confidence,
                line_confidences={},
                status=existing.status.value,
                notes=list(existing.notes or []),
            )
            logger.info(
                "region_id=%s lost a concurrent OCR insert race; reusing the winner's result",
                region.id,
            )

        ocr_results[idx] = result
        if result.status != "ok":
            any_partial = True

    return any_partial, ocr_results


def _entity_to_extracted(entity: Entity, bbox: BBox) -> ExtractedEntity:
    return ExtractedEntity(
        entity_type=entity.entity_type.value,
        normalized_value=entity.normalized_value,
        raw_text=entity.raw_text,
        confidence=entity.confidence,
        start_char=entity.start_char,
        end_char=entity.end_char,
        region_bbox=bbox,
    )


def _run_extraction_stage(
    session, db_regions: list[DBRegion], ocr_results: dict[int, OCRResult]
) -> tuple[dict[int, list[ExtractedEntity]], dict[int, list[Entity]]]:
    entities_by_region_idx: dict[int, list[ExtractedEntity]] = {}
    db_entities_by_region_idx: dict[int, list[Entity]] = {}

    for idx, region in enumerate(db_regions):
        ocr_result = ocr_results.get(idx)
        bbox = (region.bbox_x, region.bbox_y, region.bbox_w, region.bbox_h)
        if ocr_result is None or not ocr_result.text.strip():
            continue

        existing_entities = list(
            session.scalars(select(Entity).where(Entity.region_id == region.id)).all()
        )
        if existing_entities:
            entities_by_region_idx[idx] = [_entity_to_extracted(e, bbox) for e in existing_entities]
            db_entities_by_region_idx[idx] = existing_entities
            continue

        extracted = extract_entities(
            ocr_result.text, ocr_confidence=ocr_result.document_confidence, region_bbox=bbox
        )
        db_entities = []
        for e in extracted:
            date_value = parse_date_from_entity(e) if e.entity_type == "date" else None
            amount_value, amount_currency = (
                parse_amount_from_entity(e) if e.entity_type == "amount" else (None, None)
            )
            db_entity = Entity(
                region_id=region.id,
                entity_type=EntityType(e.entity_type),
                raw_text=e.raw_text,
                normalized_value=e.normalized_value,
                confidence=e.confidence,
                start_char=e.start_char,
                end_char=e.end_char,
                date_value=date_value,
                amount_value=amount_value,
                amount_currency=amount_currency,
            )
            session.add(db_entity)
            db_entities.append(db_entity)
        session.commit()

        entities_by_region_idx[idx] = extracted
        db_entities_by_region_idx[idx] = db_entities

    return entities_by_region_idx, db_entities_by_region_idx


def _update_page_full_text(session, page: Page, db_regions: list[DBRegion], ocr_results: dict[int, OCRResult]) -> None:
    ordered = sorted(enumerate(db_regions), key=lambda pair: pair[1].reading_order)
    texts = [ocr_results[idx].text for idx, _ in ordered if idx in ocr_results and ocr_results[idx].text.strip()]
    page.full_text = "\n\n".join(texts)
    session.commit()


def _run_anomaly_stage(
    session,
    document: Document,
    page: Page,
    db_regions: list[DBRegion],
    ocr_results: dict[int, OCRResult],
    entities_by_region_idx: dict[int, list[ExtractedEntity]],
    db_entities_by_region_idx: dict[int, list[Entity]],
) -> int:
    existing_count = session.scalar(
        select(func.count())
        .select_from(ReviewFlag)
        .where(ReviewFlag.document_id == document.id, ReviewFlag.page_id == page.id)
    )
    if existing_count:
        return existing_count

    layout_regions = [
        LayoutRegion(
            bbox=(r.bbox_x, r.bbox_y, r.bbox_w, r.bbox_h),
            region_type=r.region_type.value,
            reading_order=r.reading_order,
            confidence=r.confidence,
        )
        for r in db_regions
    ]

    # Best-effort mapping of an AnomalyFlag's transient region_bbox/
    # entity_raw_text back to the persisted region_id/entity_id: bbox is
    # exact (it's the same tuple this run derived both from), raw_text is
    # matched against a per-(bbox, raw_text) queue since duplicate raw text
    # within one document/region is possible.
    bbox_to_region_id = {(r.bbox_x, r.bbox_y, r.bbox_w, r.bbox_h): r.id for r in db_regions}
    entity_lookup: dict[tuple[BBox, str], list[UUID]] = {}
    for idx, db_entities in db_entities_by_region_idx.items():
        r = db_regions[idx]
        bbox = (r.bbox_x, r.bbox_y, r.bbox_w, r.bbox_h)
        for db_entity in db_entities:
            entity_lookup.setdefault((bbox, db_entity.raw_text), []).append(db_entity.id)

    all_entities = [e for entities in entities_by_region_idx.values() for e in entities]
    anomaly_flags = detect_all_anomalies(layout_regions, ocr_results, all_entities, entities_by_region_idx)

    for flag in anomaly_flags:
        region_id = bbox_to_region_id.get(flag.region_bbox) if flag.region_bbox else None
        entity_id = None
        if flag.entity_raw_text and flag.region_bbox:
            candidates = entity_lookup.get((flag.region_bbox, flag.entity_raw_text))
            if candidates:
                entity_id = candidates.pop(0)
        session.add(
            ReviewFlag(
                document_id=document.id,
                page_id=page.id,
                region_id=region_id,
                entity_id=entity_id,
                flag_type=FlagType(flag.flag_type),
                severity=FlagSeverity(flag.severity),
                explanation=flag.explanation,
            )
        )
    session.commit()
    return len(anomaly_flags)


def run_pipeline(document_id: str) -> None:
    """Runs every stage for one document, resuming from persisted state.
    Raises on an unexpected failure (worker.run_ocr_job handles job-level
    retry/backoff and marking the document `error` once retries are spent).
    """
    session = SessionLocal()
    lock_id: UUID | None = None
    try:
        document = session.get(Document, UUID(document_id))
        if document is None:
            raise ValueError(f"document {document_id} not found")
        _advisory_lock(session, document.id)
        lock_id = document.id  # only set once actually held, so `finally` doesn't unlock a lock we never acquired

        try:
            page_images = load_page_images(document.raw_image_path)
        except FileNotFoundError:
            page_images = []
        if not page_images:
            raise ValueError(f"could not read image at {document.raw_image_path!r}")

        document.status = DocumentStatus.preprocessing
        session.commit()

        total_regions = 0
        total_flags = 0
        any_partial = False

        for page_number, image in enumerate(page_images, start=1):
            with _stage_timer(document_id, f"preprocess_and_layout_p{page_number}"):
                page, db_regions, preprocessed_image = _ensure_page_and_regions(
                    session, document, image, page_number
                )
            _persist_processed_image(session, document, page, preprocessed_image, page_number)

            if not db_regions:
                _flag_empty_layout(session, document, page)
                total_flags += 1
                continue

            total_regions += len(db_regions)
            document.status = DocumentStatus.ocr_running
            session.commit()
            with _stage_timer(document_id, f"ocr_p{page_number}"):
                page_partial, ocr_results = _run_ocr_stage(session, db_regions, preprocessed_image)
            any_partial = any_partial or page_partial

            document.status = DocumentStatus.extracting
            session.commit()
            with _stage_timer(document_id, f"entity_extraction_p{page_number}"):
                entities_by_region_idx, db_entities_by_region_idx = _run_extraction_stage(
                    session, db_regions, ocr_results
                )

            with _stage_timer(document_id, f"indexing_p{page_number}"):
                _update_page_full_text(session, page, db_regions, ocr_results)

            with _stage_timer(document_id, f"anomaly_flagging_p{page_number}"):
                total_flags += _run_anomaly_stage(
                    session, document, page, db_regions, ocr_results,
                    entities_by_region_idx, db_entities_by_region_idx,
                )

        document.status = DocumentStatus.ocr_partial if any_partial else DocumentStatus.ocr_done
        session.commit()
        document.status = DocumentStatus.indexed
        session.commit()
        document.status = DocumentStatus.needs_review if total_flags else DocumentStatus.ready
        session.commit()
        inc_metric("pipeline_complete")

        logger.info(
            "pipeline_complete document_id=%s status=%s regions=%d flags=%d",
            document_id, document.status.value, total_regions, total_flags,
        )
    finally:
        if lock_id is not None:
            try:
                _advisory_unlock(session, lock_id)
            except Exception:
                logger.exception("failed to release advisory lock for document %s", lock_id)
        session.close()


def mark_document_error(document_id: str, error_message: str) -> None:
    session = SessionLocal()
    try:
        document = session.get(Document, UUID(document_id))
        if document is not None:
            document.status = DocumentStatus.error
            document.error_message = error_message[:4000]
            session.commit()
            logger.error("document_id=%s marked as error: %s", document_id, error_message)
    finally:
        session.close()
