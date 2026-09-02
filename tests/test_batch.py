from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from ingestion import batch
from storage.models import Document, DocumentStatus


def fake_settings(**overrides) -> SimpleNamespace:
    base = {
        "storage_backend": "local",
        "storage_local_path": "",
        "max_upload_size_bytes": 50 * 1024 * 1024,
        "max_image_pixels": 40_000_000,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def write_png(path, width: int = 20, height: int = 20) -> None:
    image = np.full((height, width), 255, dtype=np.uint8)
    assert cv2.imwrite(str(path), image)


@pytest.fixture()
def batch_env(monkeypatch, sqlite_session_factory, tmp_path):
    monkeypatch.setattr(batch, "SessionLocal", sqlite_session_factory)
    settings = fake_settings(storage_local_path=str(tmp_path))
    monkeypatch.setattr("ingestion.upload.get_settings", lambda: settings)
    monkeypatch.setattr("ingestion.validate.get_settings", lambda: settings)
    enqueued: list[str] = []
    monkeypatch.setattr(batch.run_ocr_job, "delay", lambda document_id: enqueued.append(document_id))
    return sqlite_session_factory, enqueued


class TestDiscoverImages:
    def test_finds_only_known_image_extensions_sorted(self, tmp_path):
        for name in ("b.png", "a.jpg", "notes.txt", "c.TIFF", "readme.md"):
            (tmp_path / name).write_bytes(b"x")

        found = batch._discover_images(tmp_path)

        assert [p.name for p in found] == ["a.jpg", "b.png", "c.TIFF"]

    def test_ignores_subdirectories(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "hidden.png").write_bytes(b"x")
        (tmp_path / "top.png").write_bytes(b"x")

        found = batch._discover_images(tmp_path)

        assert [p.name for p in found] == ["top.png"]


class TestIngestOne:
    def test_creates_document_row_and_enqueues_pipeline_job(self, batch_env, tmp_path):
        session_factory, enqueued = batch_env
        image_path = tmp_path / "scan.png"
        write_png(image_path)

        document_id = batch.ingest_one(image_path)

        assert document_id is not None
        session = session_factory()
        document = session.get(Document, document_id)
        assert document is not None
        assert document.filename == "scan.png"
        assert enqueued == [str(document_id)]

    def test_a_broker_failure_still_leaves_the_document_created(self, batch_env, tmp_path, monkeypatch):
        session_factory, _ = batch_env

        def broken_delay(document_id):
            raise ConnectionError("broker unreachable")

        monkeypatch.setattr(batch.run_ocr_job, "delay", broken_delay)
        image_path = tmp_path / "scan.png"
        write_png(image_path)

        document_id = batch.ingest_one(image_path)

        assert document_id is not None
        session = session_factory()
        document = session.get(Document, document_id)
        assert document is not None
        assert document.status == DocumentStatus.enqueue_failed

    def test_oversized_file_is_skipped_not_raised(self, batch_env, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "ingestion.upload.get_settings",
            lambda: fake_settings(storage_local_path=str(tmp_path), max_upload_size_bytes=4),
        )
        image_path = tmp_path / "big.png"
        image_path.write_bytes(b"way too big for the limit")

        document_id = batch.ingest_one(image_path)

        assert document_id is None


class TestIngestDirectory:
    def test_ingests_every_discovered_image(self, batch_env, tmp_path):
        _session_factory, enqueued = batch_env
        for i in range(5):
            write_png(tmp_path / f"scan-{i}.png")

        ingested = batch.ingest_directory(tmp_path, concurrency=2)

        assert len(ingested) == 5
        assert len(enqueued) == 5

    def test_empty_directory_ingests_nothing(self, batch_env, tmp_path):
        ingested = batch.ingest_directory(tmp_path)
        assert ingested == []

    def test_high_concurrency_does_not_drop_or_corrupt_any_ingest(self, batch_env, tmp_path):
        """Regression test for a real race: with more worker threads than
        files, each committing to the DB independently and near-
        simultaneously, a shared-connection SQLite test fixture used to
        intermittently raise sqlite3.InterfaceError ("bad parameter or
        other API misuse") -- see tests/conftest.py's sqlite_engine
        docstring. Every file must still be ingested exactly once.
        """
        session_factory, enqueued = batch_env
        count = 12
        for i in range(count):
            write_png(tmp_path / f"scan-{i}.png")

        ingested = batch.ingest_directory(tmp_path, concurrency=8)

        assert len(ingested) == count
        assert len(set(ingested)) == count  # no duplicate/reused ids
        assert len(enqueued) == count

        session = session_factory()
        assert session.query(Document).count() == count


class TestMainCLI:
    def test_rejects_a_non_directory_argument(self, tmp_path):
        not_a_dir = tmp_path / "file.png"
        not_a_dir.write_bytes(b"x")

        assert batch.main([str(not_a_dir)]) == 1
