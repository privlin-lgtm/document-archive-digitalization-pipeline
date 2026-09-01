import logging

from celery import Celery

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
