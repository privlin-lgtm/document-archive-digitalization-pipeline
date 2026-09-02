import logging
from datetime import UTC, datetime, timedelta

from celery import Celery
from sqlalchemy import select

from config import get_settings

settings = get_settings()

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

celery_app = Celery(
    "document_archive",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=900,
    task_soft_time_limit=840,
    beat_schedule={
        "reconcile-orphans": {
            "task": "worker.reconcile_orphans",
            "schedule": 120.0,
        }
    },
)


@celery_app.task(name="worker.run_ocr_job", bind=True, max_retries=3, default_retry_delay=30)
def run_ocr_job(self, document_id: str) -> None:
    """Runs the full pipeline (preprocess -> layout -> OCR -> entity
    extraction -> anomaly flagging -> indexing) for one document.

    This retry is for unexpected *job-level* failures (a DB/broker hiccup, a
    bug) -- an individual OCR backend call already gets its own retry/backoff
    inside pipeline.run. Each pipeline stage checks persisted state before
    redoing work, so retrying this task resumes rather than restarting from
    scratch. Only once retries are exhausted is the document marked `error`.
    """
    from pipeline.run import mark_document_error, run_pipeline

    try:
        run_pipeline(document_id)
    except Exception as exc:
        logger.exception(
            "pipeline run failed for document %s (attempt %d/%d)",
            document_id, self.request.retries + 1, self.max_retries + 1,
        )
        if self.request.retries >= self.max_retries:
            mark_document_error(document_id, str(exc))
            raise
        raise self.retry(exc=exc, countdown=30 * (2**self.request.retries))


@celery_app.task(name="worker.reconcile_orphans")
def reconcile_orphans() -> int:
    """Re-enqueue documents stuck in uploaded/enqueue_failed after a broker drop."""
    from storage.db import SessionLocal
    from storage.models import Document, DocumentStatus

    cutoff = datetime.now(UTC) - timedelta(minutes=2)
    session = SessionLocal()
    requeued = 0
    try:
        orphans = session.scalars(
            select(Document).where(
                Document.status.in_((DocumentStatus.uploaded, DocumentStatus.enqueue_failed)),
                Document.upload_time < cutoff,
            )
        ).all()
        for document in orphans:
            try:
                run_ocr_job.delay(str(document.id))
            except Exception:
                logger.exception("reconcile failed to enqueue document %s", document.id)
                continue
            if document.status == DocumentStatus.enqueue_failed:
                document.status = DocumentStatus.uploaded
                document.error_message = None
                session.commit()
            requeued += 1
        if requeued:
            logger.info("reconciled %d orphaned document(s)", requeued)
        return requeued
    finally:
        session.close()
