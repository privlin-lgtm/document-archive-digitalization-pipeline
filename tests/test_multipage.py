"""Multi-page sources: PDFs (rasterised with pypdfium2) and multi-frame TIFFs.

Covers the page splitter (ocr.pages), upload validation
(ingestion.validate.check_document) and the full pipeline producing one
`pages` row per page, including resuming after a crash part-way through a
document. Fixtures are built in memory with Pillow (which can write both
multi-page PDFs and multi-frame TIFFs), so no binary test files are
committed.
"""

from __future__ import annotations

import io
import uuid
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from PIL import Image
from sqlalchemy import select

import pipeline.run as pipeline_run
from ingestion import validate
from ocr import pages as ocr_pages
from storage.models import Document, DocumentStatus, OCRResultRecord, Page, Region

# Pillow writes PDF pages at this resolution, so rendering back at the same
# DPI gives page images the same size as the source bitmaps.
_PDF_DPI = 300
_PAGE_W, _PAGE_H = 1200, 900


def _paragraph_page(seed: int) -> np.ndarray:
    """A page with one dense ink block (enough for detect_regions to find a
    paragraph region), shifted per page so pages are distinguishable."""
    img = np.full((_PAGE_H, _PAGE_W), 255, dtype=np.uint8)
    y = 200 + 40 * seed
    for _ in range(5):
        cv2.rectangle(img, (150, y), (900, y + 20), 0, thickness=-1)
        y += 30
    return img


def make_pdf_bytes(page_count: int = 3) -> bytes:
    frames = [Image.fromarray(_paragraph_page(i)).convert("RGB") for i in range(page_count)]
    buf = io.BytesIO()
    frames[0].save(buf, "PDF", resolution=_PDF_DPI, save_all=True, append_images=frames[1:])
    return buf.getvalue()


def make_tiff_bytes(frame_count: int = 3, sizes: list[tuple[int, int]] | None = None) -> bytes:
    if sizes is None:
        frames = [Image.fromarray(_paragraph_page(i)) for i in range(frame_count)]
    else:
        frames = [Image.new("L", size, 255) for size in sizes]
    buf = io.BytesIO()
    frames[0].save(buf, "TIFF", save_all=True, append_images=frames[1:], compression="tiff_deflate")
    return buf.getvalue()


def make_png_bytes() -> bytes:
    ok, buffer = cv2.imencode(".png", _paragraph_page(0))
    assert ok
    return buffer.tobytes()


def fake_settings(**overrides) -> SimpleNamespace:
    base = {"max_image_pixels": 40_000_000, "max_pages_per_document": 200, "pdf_render_dpi": 300}
    base.update(overrides)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------
# ocr.pages
# --------------------------------------------------------------------------


class TestPageSplitting:
    def test_three_page_pdf_yields_three_page_images(self, tmp_path):
        path = tmp_path / "deed.pdf"
        path.write_bytes(make_pdf_bytes(3))

        assert ocr_pages.count_pages(str(path)) == 3
        images = list(ocr_pages.iter_page_images(str(path), dpi=_PDF_DPI, max_pixels=40_000_000))

        assert len(images) == 3
        for image in images:
            assert image.ndim == 3 and image.shape[2] == 3  # BGR, like cv2.imread
            # Rendered back at the DPI it was written at: same size (+-1px rounding).
            assert abs(image.shape[0] - _PAGE_H) <= 1
            assert abs(image.shape[1] - _PAGE_W) <= 1
        # Pages come out in order: the ink block moves down 40px per page.
        first_ink_rows = [int(np.argmax((img[:, :, 0] < 128).any(axis=1))) for img in images]
        assert first_ink_rows == sorted(first_ink_rows)
        assert len(set(first_ink_rows)) == 3

    def test_three_frame_tiff_yields_three_page_images(self, tmp_path):
        path = tmp_path / "ledger.tiff"
        path.write_bytes(make_tiff_bytes(3))

        assert ocr_pages.count_pages(str(path)) == 3
        images = ocr_pages.load_page_images(str(path))

        assert len(images) == 3
        assert all(img.shape == (_PAGE_H, _PAGE_W, 3) for img in images)

    def test_single_page_image_is_unchanged(self, tmp_path):
        path = tmp_path / "scan.png"
        path.write_bytes(make_png_bytes())

        assert ocr_pages.count_pages(str(path)) == 1
        images = ocr_pages.load_page_images(str(path))

        assert len(images) == 1
        np.testing.assert_array_equal(images[0], cv2.imread(str(path)))

    def test_oversized_pdf_page_is_rendered_at_a_lower_dpi_not_a_huge_bitmap(self, tmp_path):
        path = tmp_path / "plan.pdf"
        path.write_bytes(make_pdf_bytes(1))

        (image,) = ocr_pages.iter_page_images(str(path), dpi=600, max_pixels=500_000)

        assert image.shape[0] * image.shape[1] <= 500_000 * 1.01

    def test_render_scale_is_the_requested_dpi_when_it_fits(self):
        assert ocr_pages.pdf_render_scale(612, 792, 300, 40_000_000) == pytest.approx(300 / 72)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            list(ocr_pages.iter_page_images(str(tmp_path / "nope.pdf")))


# --------------------------------------------------------------------------
# ingestion.validate.check_document
# --------------------------------------------------------------------------


class TestCheckDocument:
    @pytest.fixture(autouse=True)
    def _settings(self, monkeypatch):
        self.settings = fake_settings()
        monkeypatch.setattr(validate, "get_settings", lambda: self.settings)

    def test_three_page_pdf_is_accepted_with_its_page_count(self):
        assert validate.check_document(make_pdf_bytes(3)) == 3

    def test_three_frame_tiff_is_accepted_with_its_page_count(self):
        assert validate.check_document(make_tiff_bytes(3)) == 3

    def test_single_page_image_is_one_page(self):
        assert validate.check_document(make_png_bytes()) == 1

    def test_pdf_over_the_page_limit_is_rejected(self):
        self.settings.max_pages_per_document = 2
        with pytest.raises(validate.InvalidImageError, match="3 pages, over the 2-page limit"):
            validate.check_document(make_pdf_bytes(3))

    def test_tiff_over_the_page_limit_is_rejected(self):
        self.settings.max_pages_per_document = 2
        with pytest.raises(validate.InvalidImageError, match="over the 2-page limit"):
            validate.check_document(make_tiff_bytes(3))

    def test_an_oversized_later_tiff_frame_is_rejected(self):
        # Page 1 is small; page 2 alone exceeds the pixel cap. The old check
        # only looked at the first frame.
        self.settings.max_image_pixels = 1_000_000
        content = make_tiff_bytes(sizes=[(100, 100), (2000, 1000)])
        with pytest.raises(validate.InvalidImageError, match="page 2 dimensions 2000x1000"):
            validate.check_document(content)

    def test_corrupt_pdf_is_rejected(self):
        with pytest.raises(validate.InvalidImageError, match="not a readable PDF"):
            validate.check_document(b"%PDF-1.7\nthis is not really a pdf\n")

    def test_garbage_bytes_are_rejected(self):
        with pytest.raises(validate.InvalidImageError, match="not a readable image"):
            validate.check_document(b"#!/bin/sh\necho hi\n")


# --------------------------------------------------------------------------
# pipeline.run with multi-page sources
# --------------------------------------------------------------------------


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
def make_document(monkeypatch, sqlite_session_factory, tmp_path):
    monkeypatch.setattr(pipeline_run, "SessionLocal", sqlite_session_factory)
    monkeypatch.setattr(
        "storage.paths.get_settings",
        lambda: SimpleNamespace(storage_local_path=str(tmp_path)),
    )

    def _make(filename: str, content: bytes) -> uuid.UUID:
        path = tmp_path / filename
        path.write_bytes(content)
        document_id = uuid.uuid4()
        session = sqlite_session_factory()
        session.add(
            Document(id=document_id, filename=filename, raw_image_path=str(path), status=DocumentStatus.uploaded)
        )
        session.commit()
        session.close()
        return document_id

    return _make


@pytest.fixture()
def ocr_calls(monkeypatch):
    import pytesseract

    calls: list[int] = []

    def fake_image_to_data(*a, **k):
        calls.append(1)
        return _fake_tesseract_data("Received of John Smith at Bombay the sum of $10.00")

    monkeypatch.setattr(pytesseract, "image_to_data", fake_image_to_data)
    return calls


def _pages(session, document_id) -> list[Page]:
    return list(
        session.scalars(select(Page).where(Page.document_id == document_id).order_by(Page.page_number)).all()
    )


def _assert_every_page_processed(session, document_id, expected_pages: int) -> None:
    pages = _pages(session, document_id)
    assert [p.page_number for p in pages] == list(range(1, expected_pages + 1))
    for page in pages:
        regions = session.scalars(select(Region).where(Region.page_id == page.id)).all()
        assert regions, f"page {page.page_number} has no regions"
        for region in regions:
            assert session.scalar(select(OCRResultRecord).where(OCRResultRecord.region_id == region.id))
        assert page.full_text and "John Smith" in page.full_text
        assert page.processed_image_path
        stored = cv2.imread(page.processed_image_path)
        assert stored is not None
    # Each page gets its own processed image file.
    assert len({p.processed_image_path for p in pages}) == expected_pages


class TestMultiPagePipeline:
    def test_three_page_pdf_gets_one_processed_page_row_per_page(
        self, make_document, ocr_calls, sqlite_session_factory
    ):
        document_id = make_document("deed.pdf", make_pdf_bytes(3))

        pipeline_run.run_pipeline(str(document_id))

        session = sqlite_session_factory()
        _assert_every_page_processed(session, document_id, 3)
        assert session.get(Document, document_id).status == DocumentStatus.ready
        assert len(ocr_calls) == 3  # one region per page

    def test_three_frame_tiff_gets_one_processed_page_row_per_frame(
        self, make_document, ocr_calls, sqlite_session_factory
    ):
        document_id = make_document("ledger.tiff", make_tiff_bytes(3))

        pipeline_run.run_pipeline(str(document_id))

        session = sqlite_session_factory()
        _assert_every_page_processed(session, document_id, 3)
        assert session.get(Document, document_id).status == DocumentStatus.ready

    def test_single_page_image_still_gets_exactly_one_page(
        self, make_document, ocr_calls, sqlite_session_factory
    ):
        document_id = make_document("scan.png", make_png_bytes())

        pipeline_run.run_pipeline(str(document_id))

        session = sqlite_session_factory()
        _assert_every_page_processed(session, document_id, 1)
        document = session.get(Document, document_id)
        assert document.status == DocumentStatus.ready
        # Page 1's processed image is still mirrored onto the document row.
        assert document.processed_image_path == _pages(session, document_id)[0].processed_image_path

    def test_document_over_the_page_limit_is_refused_by_the_worker_too(
        self, make_document, ocr_calls, sqlite_session_factory, monkeypatch
    ):
        real_settings = pipeline_run.get_settings()
        monkeypatch.setattr(
            pipeline_run,
            "get_settings",
            lambda: SimpleNamespace(**{**real_settings.model_dump(), "max_pages_per_document": 2}),
        )
        document_id = make_document("deed.pdf", make_pdf_bytes(3))

        with pytest.raises(ValueError, match="3 pages, over the 2-page limit"):
            pipeline_run.run_pipeline(str(document_id))

        assert ocr_calls == []
        assert _pages(sqlite_session_factory(), document_id) == []

    def test_a_crash_on_page_two_resumes_without_redoing_page_one(
        self, make_document, ocr_calls, sqlite_session_factory, monkeypatch
    ):
        document_id = make_document("deed.pdf", make_pdf_bytes(3))

        real_ocr = pipeline_run._run_ocr_stage
        stage_calls = 0

        def crash_on_second_page(*args, **kwargs):
            nonlocal stage_calls
            stage_calls += 1
            if stage_calls == 2:
                raise RuntimeError("worker killed mid-document")
            return real_ocr(*args, **kwargs)

        monkeypatch.setattr(pipeline_run, "_run_ocr_stage", crash_on_second_page)
        with pytest.raises(RuntimeError, match="worker killed"):
            pipeline_run.run_pipeline(str(document_id))

        session = sqlite_session_factory()
        pages_after_crash = _pages(session, document_id)
        assert [p.page_number for p in pages_after_crash] == [1, 2]  # page 2 laid out, not OCR'd
        page1_ocr_ids = {
            r.id
            for r in session.scalars(
                select(OCRResultRecord).join(Region).where(Region.page_id == pages_after_crash[0].id)
            )
        }
        assert len(ocr_calls) == 1
        assert session.get(Document, document_id).status != DocumentStatus.ready
        session.close()

        monkeypatch.setattr(pipeline_run, "_run_ocr_stage", real_ocr)
        pipeline_run.run_pipeline(str(document_id))

        assert len(ocr_calls) == 3  # pages 2 and 3 only on the retry
        session = sqlite_session_factory()
        _assert_every_page_processed(session, document_id, 3)
        assert session.get(Document, document_id).status == DocumentStatus.ready
        pages = _pages(session, document_id)
        assert pages[0].id == pages_after_crash[0].id  # same row, not recreated
        page1_ocr_ids_after = {
            r.id
            for r in session.scalars(select(OCRResultRecord).join(Region).where(Region.page_id == pages[0].id))
        }
        assert page1_ocr_ids_after == page1_ocr_ids
