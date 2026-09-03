"""OCR abstraction layer: multiple backends behind a common interface, with a
typed-vs-handwritten router, structured (word/box/confidence) output, and
graceful fallback when a backend fails or times out.

CLI usage:
    python -m ocr.engine run <path/to/image>
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import cv2
import numpy as np

from ocr.preprocess import to_grayscale

if TYPE_CHECKING:
    # Only imported for annotations -- torch/transformers are an optional
    # extra (see _load below), never required just to import this module.
    from transformers import TrOCRProcessor, VisionEncoderDecoderModel

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Structured result types
# --------------------------------------------------------------------------

BBox = tuple[int, int, int, int]  # (x, y, width, height)

OCRStatus = Literal["ok", "ocr_partial", "failed"]
RegionType = Literal["typed", "handwritten"]


@dataclass
class OCRWord:
    text: str
    bbox: BBox
    confidence: float  # 0-100
    line_no: int


@dataclass
class OCRResult:
    text: str
    words: list[OCRWord]
    engine: str
    document_confidence: float
    line_confidences: dict[int, float]
    status: OCRStatus = "ok"
    notes: list[str] = field(default_factory=list)


class BackendUnavailableError(Exception):
    """Raised when a backend's dependencies aren't installed."""


class BackendTimeoutError(Exception):
    """Raised when a backend exceeds its allotted run time."""


# --------------------------------------------------------------------------
# Confidence aggregation
# --------------------------------------------------------------------------


def aggregate_confidence(words: list[OCRWord]) -> tuple[float, dict[int, float]]:
    """Compute document-level and per-line confidence from word confidences."""
    if not words:
        return 0.0, {}

    doc_confidence = round(sum(w.confidence for w in words) / len(words), 2)

    by_line: dict[int, list[float]] = {}
    for word in words:
        by_line.setdefault(word.line_no, []).append(word.confidence)
    line_confidences = {
        line: round(sum(confs) / len(confs), 2) for line, confs in by_line.items()
    }
    return doc_confidence, line_confidences


# --------------------------------------------------------------------------
# Typed vs. handwritten heuristic router
# --------------------------------------------------------------------------

# Coefficient of variation (stddev/mean) of connected-component heights, above
# which a region is classified as handwritten. Typed text uses a fixed font
# size so component heights cluster tightly; handwriting varies a lot more
# (ascenders, descenders, cursive joins, inconsistent letter size).
HANDWRITING_HEIGHT_CV_THRESHOLD = 0.5

# Below this many usable components there isn't enough signal to classify
# confidently, so we default to "typed" (the cheaper, more reliable backend).
MIN_COMPONENTS_FOR_CLASSIFICATION = 4


def classify_region(image: np.ndarray) -> RegionType:
    """Lightweight heuristic: typed text has uniform character heights, so
    high variance in connected-component heights suggests handwriting.

    This is an MVP heuristic, not a learned classifier — a clear extension
    point for swapping in a trained typed/handwritten classifier later.
    """
    gray = to_grayscale(image)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    num_labels, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)

    image_area = gray.shape[0] * gray.shape[1]
    heights = [
        stats[i, cv2.CC_STAT_HEIGHT]
        for i in range(1, num_labels)  # skip background label 0
        if 4 <= stats[i, cv2.CC_STAT_AREA] <= image_area * 0.5
    ]

    if len(heights) < MIN_COMPONENTS_FOR_CLASSIFICATION:
        return "typed"

    mean_height = sum(heights) / len(heights)
    if mean_height == 0:
        return "typed"
    variance = sum((h - mean_height) ** 2 for h in heights) / len(heights)
    coefficient_of_variation = (variance**0.5) / mean_height

    return "handwritten" if coefficient_of_variation > HANDWRITING_HEIGHT_CV_THRESHOLD else "typed"


# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------


class OCRBackend(ABC):
    name: str

    @abstractmethod
    def run(self, image: np.ndarray) -> OCRResult:
        """Run OCR on a preprocessed image and return a structured result."""


class TesseractBackend(OCRBackend):
    """Typed/typewritten text backend via pytesseract.

    `psm` (page segmentation mode) defaults to 6 ("assume a single uniform
    block of text"), which suits cropped document regions; pass 4 for
    multi-column layouts or 7 for a single line.
    """

    name = "tesseract"

    def __init__(self, lang: str = "eng", psm: int = 6, extra_config: str = ""):
        self.lang = lang
        self.config = f"--psm {psm} {extra_config}".strip()

    def run(self, image: np.ndarray) -> OCRResult:
        import pytesseract

        data = pytesseract.image_to_data(
            image, lang=self.lang, config=self.config, output_type=pytesseract.Output.DICT
        )

        words: list[OCRWord] = []
        line_keys: dict[tuple[int, int, int], int] = {}

        for i, raw_text in enumerate(data["text"]):
            text = raw_text.strip()
            if not text:
                continue
            try:
                confidence = float(data["conf"][i])
            except (TypeError, ValueError):
                logger.warning("tesseract returned a non-numeric confidence for word %r; skipping", text)
                continue
            if confidence < 0:
                continue

            line_key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            line_no = line_keys.setdefault(line_key, len(line_keys))

            words.append(
                OCRWord(
                    text=text,
                    bbox=(data["left"][i], data["top"][i], data["width"][i], data["height"][i]),
                    confidence=confidence,
                    line_no=line_no,
                )
            )

        lines: dict[int, list[str]] = {}
        for word in words:
            lines.setdefault(word.line_no, []).append(word.text)
        text = "\n".join(" ".join(lines[line_no]) for line_no in sorted(lines))

        doc_confidence, line_confidences = aggregate_confidence(words)
        return OCRResult(
            text=text,
            words=words,
            engine=self.name,
            document_confidence=doc_confidence,
            line_confidences=line_confidences,
        )


class TrOCRBackend(OCRBackend):
    """Transformer-based handwriting backend (TrOCR, via HuggingFace transformers).

    Heavy dependencies (torch + model weights) are optional — install with
    `uv sync --extra handwriting`. Import is deferred to `run()` so the rest
    of the OCR module works without them installed; when missing, this
    raises BackendUnavailableError, which OCRRouter treats as a signal to
    fall back to the other backend.

    TrOCR recognizes a single pre-segmented line/region rather than
    detecting words itself, so the result is reported as one "word" spanning
    the whole input image. Confidence is the mean per-token generation
    probability from the decoder (not a calibrated OCR confidence, but a
    reasonable proxy).
    """

    name = "trocr"

    def __init__(self, model_name: str = "microsoft/trocr-base-handwritten"):
        self.model_name = model_name
        self._processor: TrOCRProcessor | None = None
        self._model: VisionEncoderDecoderModel | None = None

    def _load(self) -> None:
        try:
            import torch  # noqa: F401
            from transformers import TrOCRProcessor, VisionEncoderDecoderModel
        except ImportError as exc:
            raise BackendUnavailableError(
                "TrOCR backend requires the 'handwriting' extra "
                "(uv sync --extra handwriting): transformers/torch not installed"
            ) from exc

        self._processor = TrOCRProcessor.from_pretrained(self.model_name)
        self._model = VisionEncoderDecoderModel.from_pretrained(self.model_name)

    def run(self, image: np.ndarray) -> OCRResult:
        if self._model is None:
            self._load()
        # _load() always sets both or raises -- this just gives mypy (and a
        # future reader) the same guarantee explicitly rather than relying
        # on it inferring across the method call.
        assert self._model is not None
        assert self._processor is not None

        import torch
        from PIL import Image

        rgb = cv2.cvtColor(to_grayscale(image), cv2.COLOR_GRAY2RGB)
        pil_image = Image.fromarray(rgb)

        pixel_values = self._processor(images=pil_image, return_tensors="pt").pixel_values
        with torch.no_grad():
            output = self._model.generate(
                pixel_values, output_scores=True, return_dict_in_generate=True
            )

        text = self._processor.batch_decode(output.sequences, skip_special_tokens=True)[0].strip()

        token_probs = [
            torch.softmax(scores, dim=-1).max().item() for scores in output.scores
        ]
        confidence = round((sum(token_probs) / len(token_probs)) * 100, 2) if token_probs else 0.0

        h, w = image.shape[:2]
        words = [OCRWord(text=text, bbox=(0, 0, w, h), confidence=confidence, line_no=0)] if text else []
        doc_confidence, line_confidences = aggregate_confidence(words)

        return OCRResult(
            text=text,
            words=words,
            engine=self.name,
            document_confidence=doc_confidence,
            line_confidences=line_confidences,
        )


# --------------------------------------------------------------------------
# Router: classify region, dispatch to the right backend, fall back on failure
# --------------------------------------------------------------------------


class OCRRouter:
    def __init__(
        self,
        typed_backend: OCRBackend,
        handwriting_backend: OCRBackend,
        timeout: float = 30.0,
    ):
        self.typed_backend = typed_backend
        self.handwriting_backend = handwriting_backend
        self.timeout = timeout

    def run(self, image: np.ndarray, region_type: RegionType | None = None) -> OCRResult:
        region_type = region_type or classify_region(image)
        primary, fallback = (
            (self.typed_backend, self.handwriting_backend)
            if region_type == "typed"
            else (self.handwriting_backend, self.typed_backend)
        )

        try:
            return self._run_with_timeout(primary, image)
        except Exception as primary_error:  # noqa: BLE001 - any backend failure must fall back, not just the ones anticipated
            logger.warning("primary backend '%s' failed: %s", primary.name, primary_error)
            try:
                result = self._run_with_timeout(fallback, image)
            except Exception as fallback_error:  # noqa: BLE001 - same: report "both failed" rather than let an unanticipated error crash the pipeline job
                logger.error(
                    "both backends failed: primary '%s' (%s), fallback '%s' (%s)",
                    primary.name, primary_error, fallback.name, fallback_error,
                )
                return OCRResult(
                    text="",
                    words=[],
                    engine="none",
                    document_confidence=0.0,
                    line_confidences={},
                    status="failed",
                    notes=[
                        f"primary backend '{primary.name}' failed: {primary_error}",
                        f"fallback backend '{fallback.name}' failed: {fallback_error}",
                    ],
                )
            result.status = "ocr_partial"
            result.notes.append(
                f"primary backend '{primary.name}' failed ({primary_error}); "
                f"used fallback '{fallback.name}'"
            )
            return result

    def _run_with_timeout(self, backend: OCRBackend, image: np.ndarray) -> OCRResult:
        # Deliberately not a `with ThreadPoolExecutor(...) as executor:` block:
        # Executor.__exit__ calls shutdown(wait=True), which blocks until the
        # submitted call actually finishes — completely defeating the timeout
        # for a genuinely hung backend (verified: a 0.2s timeout against a 2s
        # hang didn't return until the full 2s). Python can't forcibly kill a
        # running thread, so on timeout we let it run to completion in the
        # background (shutdown(wait=False)) and return control immediately.
        # A true kill requires a process-based executor — deferred as follow-up.
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(backend.run, image)
        try:
            result = future.result(timeout=self.timeout)
        except FutureTimeoutError as exc:
            executor.shutdown(wait=False)
            raise BackendTimeoutError(
                f"backend '{backend.name}' timed out after {self.timeout}s"
            ) from exc
        else:
            executor.shutdown(wait=False)
            return result


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _result_to_dict(result: OCRResult) -> dict:
    return dataclasses.asdict(result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ocr.engine")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run OCR on a single image file")
    run_parser.add_argument("path")
    run_parser.add_argument("--lang", default="eng")
    run_parser.add_argument("--timeout", type=float, default=30.0)

    args = parser.parse_args(argv)

    if args.command == "run":
        image = cv2.imread(args.path, cv2.IMREAD_GRAYSCALE)
        if image is None:
            print(f"error: could not read image at {args.path}", file=sys.stderr)
            return 1

        router = OCRRouter(
            typed_backend=TesseractBackend(lang=args.lang),
            handwriting_backend=TrOCRBackend(),
            timeout=args.timeout,
        )
        result = router.run(image)
        print(json.dumps(_result_to_dict(result), indent=2))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
