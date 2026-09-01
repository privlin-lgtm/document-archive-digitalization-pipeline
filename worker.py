import logging

from celery import Celery

from config import get_settings

settings = get_settings()

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

celery_app = Celery(
    "document_archive",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)


@celery_app.task(name="worker.run_ocr_job")
def run_ocr_job(document_id: str) -> None:
    """Async entry point for the OCR pipeline stage. Wired up once
    ocr.engine.run_ocr is implemented.
    """
    raise NotImplementedError
