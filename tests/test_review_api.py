import uuid
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from config import get_settings
from review_api.main import app
from storage.db import get_db
from storage.models import (
    Document,
    DocumentStatus,
    Entity,
    EntityCorrection,
    EntityType,
    FlagSeverity,
    FlagType,
    OCRResultRecord,
    OCRStatus,
    Page,
    Region,
    RegionType,
    ReviewFlag,
    ReviewFlagStatus,
)


def auth_headers() -> dict:
    return {"Authorization": f"Bearer {get_settings().review_api_token}"}


@pytest.fixture()
def api(sqlite_session_factory, tmp_path, monkeypatch):
    def override_get_db():
        db = sqlite_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    # Never hit real Celery/Redis in tests.
    enqueued: list[str] = []
    monkeypatch.setattr("review_api.documents.run_ocr_job.delay", lambda doc_id: enqueued.append(doc_id))

    # Uploads write under a disposable temp dir instead of the real storage path.
    fake_settings = SimpleNamespace(
        storage_backend="local", storage_local_path=str(tmp_path), max_upload_size_bytes=50 * 1024 * 1024
    )
    monkeypatch.setattr("ingestion.upload.get_settings", lambda: fake_settings)

    try:
        yield TestClient(app), sqlite_session_factory, tmp_path, enqueued
    finally:
        app.dependency_overrides.clear()


def make_png_bytes(width: int = 20, height: int = 20) -> bytes:
    image = np.full((height, width), 255, dtype=np.uint8)
    ok, buffer = cv2.imencode(".png", image)
    assert ok
    return buffer.tobytes()


def seed_document_with_page_region(session_factory, *, with_entity: bool = True) -> dict:
    """Seeds a document -> page -> region -> ocr_result (+ optional entity),
    returns the ids as strings for use in requests.
    """
    session = session_factory()
    document = Document(filename="ledger.png", status=DocumentStatus.indexed, raw_image_path="/data/ledger.png")
    session.add(document)
    session.flush()

    page = Page(document_id=document.id, page_number=1, full_text="John Smith paid $45.00")
    session.add(page)
    session.flush()

    region = Region(
        page_id=page.id, bbox_x=1, bbox_y=2, bbox_w=100, bbox_h=50,
        region_type=RegionType.paragraph, reading_order=0, confidence=0.9,
    )
    session.add(region)
    session.flush()

    ocr_result = OCRResultRecord(
        region_id=region.id, engine="tesseract", text="John Smith paid $45.00",
        confidence=88.0, status=OCRStatus.ok,
    )
    session.add(ocr_result)

    entity_id = None
    if with_entity:
        entity = Entity(
            region_id=region.id, entity_type=EntityType.person, raw_text="John Smith",
            normalized_value="John Smith", confidence=0.7, start_char=0, end_char=10,
        )
        session.add(entity)
        session.flush()
        entity_id = entity.id

    session.commit()
    ids = {"document_id": str(document.id), "page_id": str(page.id), "region_id": str(region.id), "entity_id": str(entity_id) if entity_id else None}
    session.close()
    return ids


class TestUploadDocuments:
    def test_requires_auth(self, api):
        client, *_ = api
        response = client.post("/documents", files={"files": ("a.png", make_png_bytes(), "image/png")})
        assert response.status_code == 401

    def test_uploads_single_file(self, api):
        client, session_factory, *_ = api
        response = client.post(
            "/documents", headers=auth_headers(), files={"files": ("scan.png", make_png_bytes(), "image/png")}
        )
        assert response.status_code == 201
        body = response.json()
        assert len(body) == 1
        assert body[0]["filename"] == "scan.png"
        assert body[0]["status"] == "uploaded"

        session = session_factory()
        assert session.get(Document, uuid.UUID(body[0]["id"])) is not None
        session.close()

    def test_uploads_batch_of_files(self, api):
        client, *_ = api
        response = client.post(
            "/documents",
            headers=auth_headers(),
            files=[
                ("files", ("a.png", make_png_bytes(), "image/png")),
                ("files", ("b.png", make_png_bytes(), "image/png")),
            ],
        )
        assert response.status_code == 201
        assert len(response.json()) == 2

    def test_enqueues_ocr_job(self, api):
        client, _, _, enqueued = api
        response = client.post(
            "/documents", headers=auth_headers(), files={"files": ("scan.png", make_png_bytes(), "image/png")}
        )
        document_id = response.json()[0]["id"]
        assert enqueued == [document_id]

    def test_broker_failure_does_not_fail_the_upload(self, api, monkeypatch):
        client, *_ = api

        def boom(_doc_id):
            raise RuntimeError("redis unreachable")

        monkeypatch.setattr("review_api.documents.run_ocr_job.delay", boom)
        response = client.post(
            "/documents", headers=auth_headers(), files={"files": ("scan.png", make_png_bytes(), "image/png")}
        )
        assert response.status_code == 201

    def test_rejects_oversized_upload(self, api, monkeypatch):
        client, *_ = api
        tiny_settings = SimpleNamespace(storage_backend="local", storage_local_path=".", max_upload_size_bytes=10)
        monkeypatch.setattr("ingestion.upload.get_settings", lambda: tiny_settings)

        response = client.post(
            "/documents", headers=auth_headers(), files={"files": ("scan.png", make_png_bytes(), "image/png")}
        )
        assert response.status_code == 413


class TestGetDocument:
    def test_requires_auth(self, api):
        client, *_ = api
        response = client.get(f"/documents/{uuid.uuid4()}")
        assert response.status_code == 401

    def test_not_found(self, api):
        client, *_ = api
        response = client.get(f"/documents/{uuid.uuid4()}", headers=auth_headers())
        assert response.status_code == 404

    def test_returns_nested_pages_regions_entities(self, api):
        client, session_factory, *_ = api
        ids = seed_document_with_page_region(session_factory)

        response = client.get(f"/documents/{ids['document_id']}", headers=auth_headers())

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "indexed"
        assert len(body["pages"]) == 1
        page = body["pages"][0]
        assert len(page["regions"]) == 1
        region = page["regions"][0]
        assert region["ocr_result"]["text"] == "John Smith paid $45.00"
        assert len(region["entities"]) == 1
        assert region["entities"][0]["raw_text"] == "John Smith"

    def test_freshly_uploaded_document_has_empty_pages(self, api):
        client, session_factory, *_ = api
        session = session_factory()
        document = Document(filename="new.png", status=DocumentStatus.uploaded, raw_image_path="/x/new.png")
        session.add(document)
        session.commit()
        document_id = str(document.id)
        session.close()

        response = client.get(f"/documents/{document_id}", headers=auth_headers())

        assert response.status_code == 200
        assert response.json()["pages"] == []
        assert response.json()["flags"] == []


class TestGetDocumentImage:
    def test_requires_auth(self, api):
        client, *_ = api
        response = client.get(f"/documents/{uuid.uuid4()}/image")
        assert response.status_code == 401

    def test_document_not_found(self, api):
        client, *_ = api
        response = client.get(f"/documents/{uuid.uuid4()}/image", headers=auth_headers())
        assert response.status_code == 404

    def test_missing_file_on_disk_is_404(self, api):
        client, session_factory, *_ = api
        session = session_factory()
        document = Document(filename="ghost.png", status=DocumentStatus.uploaded, raw_image_path="/nowhere/ghost.png")
        session.add(document)
        session.commit()
        document_id = str(document.id)
        session.close()

        response = client.get(f"/documents/{document_id}/image", headers=auth_headers())
        assert response.status_code == 404

    def test_serves_plain_image(self, api):
        client, session_factory, tmp_path, _ = api
        image_path = tmp_path / "real.png"
        image_path.write_bytes(make_png_bytes())

        session = session_factory()
        document = Document(filename="real.png", status=DocumentStatus.uploaded, raw_image_path=str(image_path))
        session.add(document)
        session.commit()
        document_id = str(document.id)
        session.close()

        response = client.get(f"/documents/{document_id}/image", headers=auth_headers())
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"

    def test_serves_annotated_image_with_region_boxes(self, api):
        client, session_factory, tmp_path, _ = api
        image_path = tmp_path / "real.png"
        image_path.write_bytes(make_png_bytes(width=100, height=100))

        session = session_factory()
        document = Document(filename="real.png", status=DocumentStatus.uploaded, raw_image_path=str(image_path))
        session.add(document)
        session.flush()
        page = Page(document_id=document.id, page_number=1, full_text="x")
        session.add(page)
        session.flush()
        session.add(
            Region(
                page_id=page.id, bbox_x=5, bbox_y=5, bbox_w=50, bbox_h=50,
                region_type=RegionType.paragraph, reading_order=0, confidence=0.9,
            )
        )
        session.commit()
        document_id = str(document.id)
        session.close()

        plain = client.get(f"/documents/{document_id}/image?annotate=false", headers=auth_headers())
        annotated = client.get(f"/documents/{document_id}/image?annotate=true", headers=auth_headers())

        assert plain.status_code == 200
        assert annotated.status_code == 200
        assert annotated.headers["content-type"] == "image/png"
        # drawing a box changes the pixel data
        assert annotated.content != plain.content


class TestCorrectEntity:
    def test_requires_auth(self, api):
        client, *_ = api
        response = client.patch(f"/entities/{uuid.uuid4()}", json={"corrected_value": "x", "reviewer": "r"})
        assert response.status_code == 401

    def test_not_found(self, api):
        client, *_ = api
        response = client.patch(
            f"/entities/{uuid.uuid4()}", headers=auth_headers(), json={"corrected_value": "x", "reviewer": "r"}
        )
        assert response.status_code == 404

    def test_records_correction_and_updates_normalized_value(self, api):
        client, session_factory, *_ = api
        ids = seed_document_with_page_region(session_factory)

        response = client.patch(
            f"/entities/{ids['entity_id']}",
            headers=auth_headers(),
            json={"corrected_value": "John A. Smith", "reviewer": "alice@example.com"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["original_value"] == "John Smith"
        assert body["corrected_value"] == "John A. Smith"
        assert body["reviewer"] == "alice@example.com"

        session = session_factory()
        entity = session.get(Entity, uuid.UUID(ids["entity_id"]))
        assert entity.normalized_value == "John A. Smith"
        assert entity.raw_text == "John Smith"  # original OCR output untouched
        session.close()

    def test_multiple_corrections_build_a_history(self, api):
        client, session_factory, *_ = api
        ids = seed_document_with_page_region(session_factory)

        client.patch(
            f"/entities/{ids['entity_id']}",
            headers=auth_headers(),
            json={"corrected_value": "John A. Smith", "reviewer": "alice"},
        )
        second = client.patch(
            f"/entities/{ids['entity_id']}",
            headers=auth_headers(),
            json={"corrected_value": "Jonathan Smith", "reviewer": "bob"},
        )

        assert second.json()["original_value"] == "John A. Smith"  # picks up alice's edit, not the raw OCR value

        session = session_factory()
        corrections = session.query(EntityCorrection).all()
        assert len(corrections) == 2
        session.close()


class TestUpdateReviewFlag:
    def _seed_flag(self, session_factory) -> str:
        session = session_factory()
        document = Document(filename="a.png", status=DocumentStatus.needs_review, raw_image_path="/x/a.png")
        session.add(document)
        session.flush()
        flag = ReviewFlag(
            document_id=document.id, flag_type=FlagType.low_ocr_confidence, severity=FlagSeverity.medium,
            explanation="test", status=ReviewFlagStatus.open,
        )
        session.add(flag)
        session.commit()
        flag_id = str(flag.id)
        session.close()
        return flag_id

    def test_requires_auth(self, api):
        client, *_ = api
        response = client.patch(f"/review_flags/{uuid.uuid4()}", json={"status": "resolved"})
        assert response.status_code == 401

    def test_not_found(self, api):
        client, *_ = api
        response = client.patch(f"/review_flags/{uuid.uuid4()}", headers=auth_headers(), json={"status": "resolved"})
        assert response.status_code == 404

    def test_resolve_sets_status_and_timestamp(self, api):
        client, session_factory, *_ = api
        flag_id = self._seed_flag(session_factory)

        response = client.patch(f"/review_flags/{flag_id}", headers=auth_headers(), json={"status": "resolved"})

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "resolved"
        assert body["status_changed_at"] is not None

    def test_dismiss(self, api):
        client, session_factory, *_ = api
        flag_id = self._seed_flag(session_factory)

        response = client.patch(f"/review_flags/{flag_id}", headers=auth_headers(), json={"status": "dismissed"})

        assert response.status_code == 200
        assert response.json()["status"] == "dismissed"

    def test_rejects_invalid_target_status(self, api):
        client, session_factory, *_ = api
        flag_id = self._seed_flag(session_factory)

        response = client.patch(f"/review_flags/{flag_id}", headers=auth_headers(), json={"status": "open"})

        assert response.status_code == 422


class TestStats:
    def test_requires_auth(self, api):
        client, *_ = api
        assert client.get("/stats").status_code == 401

    def test_reflects_seeded_data(self, api):
        client, session_factory, *_ = api
        seed_document_with_page_region(session_factory)

        response = client.get("/stats", headers=auth_headers())

        assert response.status_code == 200
        body = response.json()
        assert body["total_documents"] == 1
        assert body["documents_indexed"] == 1
        assert body["average_ocr_confidence"] == pytest.approx(88.0)
        assert body["average_entity_confidence"] == pytest.approx(0.7)

    def test_empty_database_returns_zeros_not_errors(self, api):
        client, *_ = api
        response = client.get("/stats", headers=auth_headers())
        assert response.status_code == 200
        body = response.json()
        assert body["total_documents"] == 0
        assert body["average_ocr_confidence"] is None
        assert body["open_flags_by_type"] == {}


# --------------------------------------------------------------------------
# GET /search — needs real Postgres (tsvector/pg_trgm); skipped otherwise,
# mirroring tests/test_queries.py's pattern.
# --------------------------------------------------------------------------


def _make_postgres_session_factory():
    try:
        engine = create_engine(get_settings().database_url, pool_pre_ping=True)
        with engine.connect() as conn:
            has_seed_data = conn.execute(
                text("SELECT 1 FROM documents WHERE filename = 'ledger_1897_smith_bombay.png'")
            ).first()
        if not has_seed_data:
            return None
    except Exception:  # noqa: BLE001 - any connectivity failure means "skip", not "fail"
        return None
    return sessionmaker(bind=engine)


_pg_session_factory = _make_postgres_session_factory()


@pytest.mark.skipif(
    _pg_session_factory is None,
    reason="requires a live Postgres with the stage 5 schema + scripts/seed_synthetic_data.sql applied",
)
class TestSearchEndpoint:
    @pytest.fixture()
    def pg_client(self, monkeypatch):
        def override_get_db():
            db = _pg_session_factory()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.clear()

    def test_requires_auth(self, pg_client):
        assert pg_client.get("/search", params={"q": "John Smith"}).status_code == 401

    def test_finds_seeded_document(self, pg_client):
        response = pg_client.get("/search", params={"q": "John Smith Bombay"}, headers=auth_headers())
        assert response.status_code == 200
        body = response.json()
        assert body["total"] >= 1
        assert any(r["filename"] == "ledger_1897_smith_bombay.png" for r in body["results"])

    def test_date_range_filter_excludes_out_of_range(self, pg_client):
        response = pg_client.get(
            "/search",
            params={"q": "John Smith", "date_from": "1600-01-01", "date_to": "1600-12-31"},
            headers=auth_headers(),
        )
        assert response.status_code == 200
        assert response.json()["results"] == []

    def test_location_filter(self, pg_client):
        response = pg_client.get(
            "/search", params={"q": "paid", "location": "Bombay"}, headers=auth_headers()
        )
        assert response.status_code == 200
        assert any(r["filename"] == "ledger_1897_smith_bombay.png" for r in response.json()["results"])

    def test_missing_query_param_is_422(self, pg_client):
        response = pg_client.get("/search", headers=auth_headers())
        assert response.status_code == 422
