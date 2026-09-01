import time

import cv2
import numpy as np
import pytest

from ocr.engine import (
    BackendUnavailableError,
    OCRBackend,
    OCRResult,
    OCRRouter,
    OCRWord,
    TesseractBackend,
    TrOCRBackend,
    aggregate_confidence,
    classify_region,
)


def make_typed_image(width: int = 400, height: int = 150) -> np.ndarray:
    img = np.full((height, width), 255, dtype=np.uint8)
    for y in (40, 80, 120):
        cv2.putText(
            img, "Typed Document Text", (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, 0, 2, cv2.LINE_AA
        )
    return img


def make_handwritten_like_image(width: int = 400, height: int = 200, seed: int = 0) -> np.ndarray:
    """Simulate irregular handwriting: many blobs of widely varying height."""
    img = np.full((height, width), 255, dtype=np.uint8)
    rng = np.random.default_rng(seed)
    x = 20
    while x < width - 40:
        h = int(rng.integers(8, 70))
        w = int(rng.integers(10, 30))
        y = height // 2 - h // 2 + int(rng.integers(-15, 15))
        cv2.ellipse(img, (x + w // 2, y + h // 2), (max(w // 2, 2), max(h // 2, 2)), 0, 0, 360, 0, -1)
        x += w + int(rng.integers(5, 15))
    return img


# --------------------------------------------------------------------------
# aggregate_confidence
# --------------------------------------------------------------------------


class TestAggregateConfidence:
    def test_empty_words(self):
        doc_conf, line_conf = aggregate_confidence([])
        assert doc_conf == 0.0
        assert line_conf == {}

    def test_averages_by_document_and_line(self):
        words = [
            OCRWord(text="a", bbox=(0, 0, 1, 1), confidence=90.0, line_no=0),
            OCRWord(text="b", bbox=(0, 0, 1, 1), confidence=70.0, line_no=0),
            OCRWord(text="c", bbox=(0, 0, 1, 1), confidence=50.0, line_no=1),
        ]
        doc_conf, line_conf = aggregate_confidence(words)
        assert doc_conf == pytest.approx((90 + 70 + 50) / 3)
        assert line_conf == {0: pytest.approx(80.0), 1: pytest.approx(50.0)}


# --------------------------------------------------------------------------
# classify_region
# --------------------------------------------------------------------------


class TestClassifyRegion:
    def test_typed_text_classified_as_typed(self):
        assert classify_region(make_typed_image()) == "typed"

    def test_irregular_blobs_classified_as_handwritten(self):
        assert classify_region(make_handwritten_like_image()) == "handwritten"

    def test_blank_image_defaults_to_typed(self):
        blank = np.full((100, 100), 255, dtype=np.uint8)
        assert classify_region(blank) == "typed"


# --------------------------------------------------------------------------
# TesseractBackend (mocked pytesseract)
# --------------------------------------------------------------------------


class TestTesseractBackend:
    def test_parses_mocked_output_into_structured_result(self, monkeypatch):
        import pytesseract

        fake_data = {
            "block_num": [1, 1, 1, 1],
            "par_num": [1, 1, 1, 1],
            "line_num": [1, 1, 2, 2],
            "left": [10, 70, 10, 60],
            "top": [10, 10, 40, 40],
            "width": [50, 40, 45, 50],
            "height": [20, 20, 20, 20],
            "conf": ["-1", "95", "40", "88"],
            "text": ["", "Hello", "World", "There"],
        }

        def fake_image_to_data(image, lang=None, config=None, output_type=None):
            return fake_data

        monkeypatch.setattr(pytesseract, "image_to_data", fake_image_to_data)

        backend = TesseractBackend()
        result = backend.run(np.zeros((100, 100), dtype=np.uint8))

        assert result.engine == "tesseract"
        assert result.status == "ok"
        assert result.text == "Hello\nWorld There"
        assert len(result.words) == 3
        assert result.words[0] == OCRWord(text="Hello", bbox=(70, 10, 40, 20), confidence=95.0, line_no=0)
        assert result.words[1].line_no == 1
        assert result.document_confidence == pytest.approx((95 + 40 + 88) / 3, abs=0.01)
        assert result.line_confidences[0] == pytest.approx(95.0)
        assert result.line_confidences[1] == pytest.approx((40 + 88) / 2)

    def test_skips_low_confidence_placeholder_rows(self, monkeypatch):
        import pytesseract

        fake_data = {
            "block_num": [1],
            "par_num": [1],
            "line_num": [1],
            "left": [0],
            "top": [0],
            "width": [0],
            "height": [0],
            "conf": ["-1"],
            "text": [""],
        }
        monkeypatch.setattr(
            pytesseract, "image_to_data", lambda *a, **k: fake_data
        )

        backend = TesseractBackend()
        result = backend.run(np.zeros((10, 10), dtype=np.uint8))
        assert result.words == []
        assert result.text == ""
        assert result.document_confidence == 0.0

    def test_non_numeric_confidence_is_skipped_not_fatal(self, monkeypatch):
        """A malformed conf value from tesseract must not crash the whole
        OCR call — it should just skip that one row.
        """
        import pytesseract

        fake_data = {
            "block_num": [1, 1],
            "par_num": [1, 1],
            "line_num": [1, 1],
            "left": [0, 20],
            "top": [0, 0],
            "width": [10, 10],
            "height": [10, 10],
            "conf": ["not-a-number", "90"],
            "text": ["Bad", "Good"],
        }
        monkeypatch.setattr(pytesseract, "image_to_data", lambda *a, **k: fake_data)

        backend = TesseractBackend()
        result = backend.run(np.zeros((10, 10), dtype=np.uint8))

        assert [w.text for w in result.words] == ["Good"]
        assert result.status == "ok"


# --------------------------------------------------------------------------
# TrOCRBackend (unavailable without the optional 'handwriting' extra)
# --------------------------------------------------------------------------


class TestTrOCRBackend:
    def test_unavailable_without_transformers_installed(self):
        backend = TrOCRBackend()
        with pytest.raises(BackendUnavailableError):
            backend.run(np.zeros((50, 50), dtype=np.uint8))


# --------------------------------------------------------------------------
# OCRRouter: dispatch, fallback, timeout
# --------------------------------------------------------------------------


class FixedResultBackend(OCRBackend):
    def __init__(self, name: str, confidence: float = 90.0):
        self.name = name
        self.confidence = confidence

    def run(self, image: np.ndarray) -> OCRResult:
        word = OCRWord(text="ok", bbox=(0, 0, 1, 1), confidence=self.confidence, line_no=0)
        doc_conf, line_conf = aggregate_confidence([word])
        return OCRResult(
            text="ok", words=[word], engine=self.name, document_confidence=doc_conf, line_confidences=line_conf
        )


class AlwaysFailBackend(OCRBackend):
    name = "always_fail"

    def run(self, image: np.ndarray) -> OCRResult:
        raise RuntimeError("backend exploded")


class SlowBackend(OCRBackend):
    name = "slow"

    def __init__(self, sleep_seconds: float = 1.0):
        self.sleep_seconds = sleep_seconds

    def run(self, image: np.ndarray) -> OCRResult:
        time.sleep(self.sleep_seconds)
        return OCRResult(text="late", words=[], engine=self.name, document_confidence=0.0, line_confidences={})


class TestOCRRouter:
    def test_primary_success_returns_ok_status(self):
        router = OCRRouter(
            typed_backend=FixedResultBackend("typed_ok"),
            handwriting_backend=AlwaysFailBackend(),
        )
        result = router.run(np.zeros((10, 10), dtype=np.uint8), region_type="typed")
        assert result.status == "ok"
        assert result.engine == "typed_ok"

    def test_primary_failure_falls_back_and_marks_partial(self):
        router = OCRRouter(
            typed_backend=AlwaysFailBackend(),
            handwriting_backend=FixedResultBackend("handwriting_ok"),
        )
        result = router.run(np.zeros((10, 10), dtype=np.uint8), region_type="typed")
        assert result.status == "ocr_partial"
        assert result.engine == "handwriting_ok"
        assert result.notes

    def test_both_backends_fail_returns_failed_status(self):
        router = OCRRouter(
            typed_backend=AlwaysFailBackend(),
            handwriting_backend=AlwaysFailBackend(),
        )
        result = router.run(np.zeros((10, 10), dtype=np.uint8), region_type="typed")
        assert result.status == "failed"
        assert result.document_confidence == 0.0
        assert result.words == []
        assert len(result.notes) == 2

    def test_slow_primary_times_out_and_falls_back(self):
        router = OCRRouter(
            typed_backend=SlowBackend(),
            handwriting_backend=FixedResultBackend("fast_fallback"),
            timeout=0.1,
        )
        result = router.run(np.zeros((10, 10), dtype=np.uint8), region_type="typed")
        assert result.status == "ocr_partial"
        assert result.engine == "fast_fallback"

    def test_timeout_does_not_block_for_the_full_hang_duration(self):
        """Regression test: _run_with_timeout previously used
        `with ThreadPoolExecutor(...) as executor:`, whose __exit__ calls
        shutdown(wait=True) — which blocks until the hung call actually
        finishes, silently defeating the configured timeout. A router with
        timeout=0.2s against a 3s-hanging backend must return promptly, not
        after ~3s.
        """
        router = OCRRouter(
            typed_backend=SlowBackend(sleep_seconds=3.0),
            handwriting_backend=FixedResultBackend("fast_fallback"),
            timeout=0.2,
        )
        start = time.monotonic()
        result = router.run(np.zeros((10, 10), dtype=np.uint8), region_type="typed")
        elapsed = time.monotonic() - start

        assert elapsed < 1.0, f"router.run took {elapsed:.2f}s, expected well under the 3s hang"
        assert result.status == "ocr_partial"
        assert result.engine == "fast_fallback"

    def test_handwritten_region_dispatches_to_handwriting_backend_first(self):
        router = OCRRouter(
            typed_backend=FixedResultBackend("typed"),
            handwriting_backend=FixedResultBackend("handwriting"),
        )
        result = router.run(np.zeros((10, 10), dtype=np.uint8), region_type="handwritten")
        assert result.engine == "handwriting"
        assert result.status == "ok"

    def test_unavailable_trocr_backend_falls_back_to_tesseract(self, monkeypatch):
        import pytesseract

        fake_data = {
            "block_num": [1],
            "par_num": [1],
            "line_num": [1],
            "left": [0],
            "top": [0],
            "width": [10],
            "height": [10],
            "conf": ["90"],
            "text": ["fallback"],
        }
        monkeypatch.setattr(pytesseract, "image_to_data", lambda *a, **k: fake_data)

        router = OCRRouter(typed_backend=TesseractBackend(), handwriting_backend=TrOCRBackend())
        result = router.run(np.zeros((10, 10), dtype=np.uint8), region_type="handwritten")

        assert result.status == "ocr_partial"
        assert result.engine == "tesseract"
        assert result.text == "fallback"
