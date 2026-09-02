"""Batch ingestion CLI: point at a directory of scanned images and enqueue
each for the async pipeline (see pipeline.run / worker.run_ocr_job).
Archives are typically received as a folder of hundreds of scans dropped in
at once (a completed digitization run, a delivered hard drive), not one file
at a time through the review UI's upload form.

`--concurrency` bounds this script's own file-save/DB-insert/enqueue work,
so a folder of hundreds of scans doesn't open hundreds of files and DB
connections at once. It does *not* control how many OCR jobs run at once --
that's the Celery worker's own concurrency (`celery worker --concurrency=N`,
see docker-compose.yml), which is the real lever for "avoid overwhelming the
OCR backend" once jobs are already queued.

CLI usage:
    python -m ingestion.batch <dir>
    python -m ingestion.batch <dir> --concurrency 8
    python -m ingestion.batch <dir> --watch --poll-interval 30
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from config import get_settings
from ingestion.upload import UnsafeFilenameError, UploadTooLargeError, save_raw_image
from ingestion.validate import InvalidImageError, decode_and_check_image
from storage.db import SessionLocal
from storage.models import Document, DocumentStatus
from worker import run_ocr_job

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}

DEFAULT_CONCURRENCY = 4
DEFAULT_POLL_INTERVAL_SECONDS = 10.0


def _discover_images(directory: Path) -> list[Path]:
    return sorted(p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS)


def ingest_one(path: Path) -> uuid.UUID | None:
    """Save one scan to storage, create its `documents` row, and enqueue its
    pipeline job. Returns the new document id, or None if ingestion failed
    (logged, not raised -- one bad file shouldn't abort the whole batch).
    """
    document_id = uuid.uuid4()
    content = path.read_bytes()
    try:
        decode_and_check_image(content)
        raw_image_path = save_raw_image(document_id, path.name, content)
    except (UnsafeFilenameError, UploadTooLargeError, InvalidImageError):
        logger.exception("skipping %s: could not be saved", path)
        return None

    session = SessionLocal()
    try:
        session.add(
            Document(
                id=document_id, filename=path.name, raw_image_path=raw_image_path, status=DocumentStatus.uploaded
            )
        )
        session.commit()
    except Exception:
        logger.exception("skipping %s: could not create its documents row", path)
        return None
    finally:
        session.close()

    session = SessionLocal()
    try:
        run_ocr_job.delay(str(document_id))
    except Exception:
        logger.exception(
            "document %s (%s) was created but its pipeline job failed to enqueue",
            document_id, path,
        )
        document = session.get(Document, document_id)
        if document is not None:
            document.status = DocumentStatus.enqueue_failed
            document.error_message = "failed to enqueue pipeline job"
            session.commit()
    finally:
        session.close()
    return document_id


def ingest_directory(directory: Path, *, concurrency: int = DEFAULT_CONCURRENCY) -> list[uuid.UUID]:
    paths = _discover_images(directory)
    logger.info("found %d image(s) in %s", len(paths), directory)

    ingested: list[uuid.UUID] = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(ingest_one, p): p for p in paths}
        for future in as_completed(futures):
            path = futures[future]
            try:
                document_id = future.result()
            except Exception:
                logger.exception("unexpected error ingesting %s", path)
                continue
            if document_id is not None:
                ingested.append(document_id)

    logger.info("ingested %d/%d file(s) from %s", len(ingested), len(paths), directory)
    return ingested


def watch_directory(directory: Path, *, concurrency: int, poll_interval: float) -> None:
    """Poll `directory` for filenames not yet seen, ingesting each as it
    appears. A file is only marked seen after a successful ingest so a
    transient failure is retried on the next poll.
    """
    seen: set[str] = set()
    logger.info("watching %s every %.0fs for new scans (Ctrl+C to stop)", directory, poll_interval)
    while True:
        new_paths = [p for p in _discover_images(directory) if p.name not in seen]
        if new_paths:
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = {executor.submit(ingest_one, p): p for p in new_paths}
                for future in as_completed(futures):
                    path = futures[future]
                    try:
                        document_id = future.result()
                    except Exception:
                        logger.exception("unexpected error during watched ingest of %s", path)
                        continue
                    if document_id is not None:
                        seen.add(path.name)
        time.sleep(poll_interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ingestion.batch")
    parser.add_argument("directory", type=Path, help="Directory of scanned images to ingest")
    parser.add_argument(
        "--concurrency", type=int, default=DEFAULT_CONCURRENCY, help="Max concurrent file ingests"
    )
    parser.add_argument("--watch", action="store_true", help="Keep watching the directory for new files")
    parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL_SECONDS)
    args = parser.parse_args(argv)

    logging.basicConfig(level=get_settings().log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if not args.directory.is_dir():
        print(f"error: {args.directory} is not a directory", file=sys.stderr)
        return 1

    if args.watch:
        watch_directory(args.directory, concurrency=args.concurrency, poll_interval=args.poll_interval)
        return 0

    ingest_directory(args.directory, concurrency=args.concurrency)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
