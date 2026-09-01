"""pipeline.run integration tests, against the shared SQLite fixture (see
conftest.py) with pytesseract mocked -- same convention as
tests/test_ocr_engine.py: the OCR *backend* is the external dependency
being stubbed out, everything else (preprocessing, layout detection, the
router, DB persistence, entity extraction, anomaly detection, status
transitions) runs for real.
"""

import uuid

import cv2
import numpy as np
import pytest
from sqlalchemy import select

import pipeline.run as pipeline_run
from storage.models import Document, DocumentStatus, Entity, Page, ReviewFlag


def _paragraph_block_image(width: int = 1200, height: int = 900) -> np.ndarray:
    """One dense ink block -- enough for ocr.layout.detect_regions to find a
    single paragraph-classified region (mirrors tests/test_layout.py).
    """
    img = np.full((height, width), 255, dtype=np.uint8)
    y = 300
    for _ in range(5):
        cv2.rectangle(img, (150, y), (900, y + 20), 0, thickness=-1)
        y += 30
    return img


def _fake_tesseract_data(text: str, confidence: float = 92.0) -> dict:
    words = text.split()
    n = len(words)
    return {
        "text": words,
        "conf": [confidence] * n,
        "left": [10 * i for i in range(n)],
        "top": [10] * n,
        "width": [8] * n,
        "height": [12] * n,
        "block_num": [1] * n,
        "par_num": [1] * n,
        "line_num": [1] * n,
    }


@pytest.fixture()
def pipeline_env(monkeypatch, sqlite_session_factory, tmp_path):
    """Wires pipeline.run's module-level SessionLocal to the SQLite fixture
    and creates one `documents` row pointing at a real (throwaway) image on
    disk -- cv2.imread needs an actual file.
    """
    monkeypatch.setattr(pipeline_run, "SessionLocal", sqlite_session_factory)

    image_path = tmp_path / "scan.png"
    cv2.imwrite(str(image_path), _paragraph_block_image())

    document_id = uuid.uuid4()
    session = sqlite_session_factory()
    session.add(
        Document(
            id=document_id,
            filename="scan.png",
            raw_image_path=str(image_path),
            status=DocumentStatus.uploaded,
        )
    )
    session.commit()
    session.close()

    return document_id, sqlite_session_factory


class TestHappyPath:
    def test_clean_document_reaches_ready_with_entities_and_no_flags(self, pipeline_env, monkeypatch):
        import pytesseract

        document_id, session_factory = pipeline_env
        monkeypatch.setattr(
            pytesseract,
            "image_to_data",
            lambda *a, **k: _fake_tesseract_data(
                "Report filed by John Smith on the 3rd day of March, 1897. Amount received: $128.50"
            ),
        )

        pipeline_run.run_pipeline(str(document_id))

        session = session_factory()
        document = session.get(Document, document_id)
        assert document.status == DocumentStatus.ready

        entities = session.scalars(select(Entity)).all()
        assert len(entities) > 0

        flags = session.scalars(select(ReviewFlag)).all()
        assert flags == []

        page = session.scalars(select(Page).where(Page.document_id == document_id)).first()
        assert page.full_text.strip() != ""

    def test_low_confidence_ocr_reaches_needs_review_with_a_flag(self, pipeline_env, monkeypatch):
        import pytesseract

        document_id, session_factory = pipeline_env
        monkeypatch.setattr(
            pytesseract, "image_to_data", lambda *a, **k: _fake_tesseract_data("xqz", confidence=2.0)
        )

        pipeline_run.run_pipeline(str(document_id))

        session = session_factory()
        document = session.get(Document, document_id)
        assert document.status == DocumentStatus.needs_review

        flags = session.scalars(select(ReviewFlag)).all()
        assert len(flags) >= 1
        assert any(f.flag_type.value == "illegible" for f in flags)

    def test_implausible_amount_is_flagged_as_entity_conflict(self, pipeline_env, monkeypatch):
        import pytesseract

        document_id, session_factory = pipeline_env
        monkeypatch.setattr(
            pytesseract,
            "image_to_data",
            lambda *a, **k: _fake_tesseract_data("Amount paid to the estate: $50,000,000.00"),
        )

        pipeline_run.run_pipeline(str(document_id))

        session = session_factory()
        document = session.get(Document, document_id)
        assert document.status == DocumentStatus.needs_review

        flags = session.scalars(select(ReviewFlag)).all()
        assert any(f.flag_type.value == "entity_conflict" for f in flags)


class TestResumability:
    def test_rerunning_a_completed_document_does_not_call_ocr_again(self, pipeline_env, monkeypatch):
        import pytesseract

        document_id, _session_factory = pipeline_env
        call_count = 0

        def fake_image_to_data(*a, **k):
            nonlocal call_count
            call_count += 1
            return _fake_tesseract_data("John Smith, Bombay, $10.00")

        monkeypatch.setattr(pytesseract, "image_to_data", fake_image_to_data)

        pipeline_run.run_pipeline(str(document_id))
        assert call_count == 1

        pipeline_run.run_pipeline(str(document_id))
        assert call_count == 1  # OCR result already persisted -- not redone

    def test_a_region_ocr_d_in_a_prior_run_is_reused_after_a_mid_pipeline_crash(
        self, pipeline_env, monkeypatch
    ):
        """Simulates a crash after OCR persisted but before the document
        reached a terminal status: OCR must not run again on retry.
        """
        import pytesseract

        document_id, session_factory = pipeline_env
        call_count = 0

        def fake_image_to_data(*a, **k):
            nonlocal call_count
            call_count += 1
            return _fake_tesseract_data("John Smith, Bombay, $10.00")

        monkeypatch.setattr(pytesseract, "image_to_data", fake_image_to_data)

        pipeline_run.run_pipeline(str(document_id))
        assert call_count == 1

        # Roll the document back to an earlier-looking state, as if a crash
        # happened right after OCR but before extraction/flagging completed.
        session = session_factory()
        document = session.get(Document, document_id)
        document.status = DocumentStatus.ocr_done
        session.commit()
        session.close()

        pipeline_run.run_pipeline(str(document_id))
        assert call_count == 1  # still not redone -- resumed from persisted OCR


class TestErrorHandling:
    def test_missing_document_raises(self, pipeline_env):
        with pytest.raises(ValueError, match="not found"):
            pipeline_run.run_pipeline(str(uuid.uuid4()))

    def test_unreadable_image_raises(self, pipeline_env):
        document_id, session_factory = pipeline_env
        session = session_factory()
        document = session.get(Document, document_id)
        document.raw_image_path = "/does/not/exist.png"
        session.commit()
        session.close()

        with pytest.raises(ValueError, match="could not read image"):
            pipeline_run.run_pipeline(str(document_id))

    def test_mark_document_error_sets_status(self, pipeline_env):
        document_id, session_factory = pipeline_env
        pipeline_run.mark_document_error(str(document_id), "boom")

        session = session_factory()
        document = session.get(Document, document_id)
        assert document.status == DocumentStatus.error
