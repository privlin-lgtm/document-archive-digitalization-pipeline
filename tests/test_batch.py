from types import SimpleNamespace

import pytest

from ingestion import batch
from storage.models import Document


def fake_settings(**overrides) -> SimpleNamespace:
    base = {"storage_backend": "local", "storage_local_path": "", "max_upload_size_bytes": 50 * 1024 * 1024}
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture()
def batch_env(monkeypatch, sqlite_session_factory, tmp_path):
    monkeypatch.setattr(batch, "SessionLocal", sqlite_session_factory)
    monkeypatch.setattr("ingestion.upload.get_settings", lambda: fake_settings(storage_local_path=str(tmp_path)))
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
        image_path.write_bytes(b"fake-image-bytes")

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
        image_path.write_bytes(b"fake-image-bytes")

        document_id = batch.ingest_one(image_path)

        assert document_id is not None
        session = session_factory()
        assert session.get(Document, document_id) is not None

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
            (tmp_path / f"scan-{i}.png").write_bytes(b"x")

        ingested = batch.ingest_directory(tmp_path, concurrency=2)

        assert len(ingested) == 5
        assert len(enqueued) == 5

    def test_empty_directory_ingests_nothing(self, batch_env, tmp_path):
        ingested = batch.ingest_directory(tmp_path)
        assert ingested == []


class TestMainCLI:
    def test_rejects_a_non_directory_argument(self, tmp_path):
        not_a_dir = tmp_path / "file.png"
        not_a_dir.write_bytes(b"x")

        assert batch.main([str(not_a_dir)]) == 1
