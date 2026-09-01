"""Image preprocessing pipeline that prepares scanned document images for OCR.

Each step is a plain function taking and returning a numpy array (grayscale,
uint8), so steps can be composed and toggled independently per document type
via the `steps` argument of `preprocess`.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import cv2
import numpy as np

# Below this contrast (stddev of pixel intensities), CLAHE is applied as a
# fallback before binarization to recover faded/low-contrast documents.
LOW_CONTRAST_STDDEV_THRESHOLD = 40.0

# Deskew search range and resolution, in degrees.
DESKEW_ANGLE_RANGE = 15.0
DESKEW_ANGLE_STEP = 0.5


def to_grayscale(image: np.ndarray) -> np.ndarray:
    """Convert a BGR/RGB image to single-channel grayscale. No-op if already gray."""
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def _projection_profile_score(binary: np.ndarray, angle: float) -> float:
    """Score a rotation angle by how sharply text rows separate from gaps.

    Rotating a page to its true orientation makes horizontal row-sums of ink
    pixels peaky (text lines vs. white gaps); the variance of those row-sums
    is maximized near the correct deskew angle.
    """
    h, w = binary.shape
    center = (w / 2, h / 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(
        binary, matrix, (w, h), flags=cv2.INTER_NEAREST, borderValue=0
    )
    row_sums = rotated.sum(axis=1).astype(np.float64)
    return float(row_sums.var())


def estimate_skew_angle(image: np.ndarray) -> float:
    """Estimate the rotation (in degrees) needed to deskew a scanned page.

    Uses the projection-profile method: binarize, then search a range of
    candidate angles for the one that maximizes row-sum variance (i.e. text
    lines are most sharply separated from background gaps).
    """
    gray = to_grayscale(image)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    best_angle = 0.0
    best_score = -1.0
    angle = -DESKEW_ANGLE_RANGE
    while angle <= DESKEW_ANGLE_RANGE:
        score = _projection_profile_score(binary, angle)
        if score > best_score:
            best_score = score
            best_angle = angle
        angle += DESKEW_ANGLE_STEP
    return best_angle


def deskew(image: np.ndarray) -> np.ndarray:
    """Rotate the image to correct skew, estimated via the projection-profile method."""
    angle = estimate_skew_angle(image)
    if abs(angle) < 1e-6:
        return image
    h, w = image.shape[:2]
    center = (w / 2, h / 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    border_value = int(np.median(image))
    return cv2.warpAffine(
        image, matrix, (w, h), flags=cv2.INTER_CUBIC, borderValue=border_value
    )


def denoise(image: np.ndarray) -> np.ndarray:
    """Denoise while preserving edges, so faint handwriting strokes survive.

    Bilateral filtering smooths flat regions (paper texture, scanner noise)
    without blurring across strong edges like ink strokes.
    """
    return cv2.bilateralFilter(image, d=9, sigmaColor=75, sigmaSpace=75)


def clahe_enhance(image: np.ndarray) -> np.ndarray:
    """Contrast-limited adaptive histogram equalization, for faded/low-contrast pages."""
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    return clahe.apply(image)


def binarize(image: np.ndarray) -> np.ndarray:
    """Adaptive thresholding tuned for aged, unevenly-lit paper.

    A Gaussian-weighted local threshold (rather than a single global one)
    copes with lighting gradients and staining across an aged page.
    """
    return cv2.adaptiveThreshold(
        image,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=35,
        C=15,
    )


def auto_crop(
    image: np.ndarray, dark_mean_threshold: float = 40.0, max_trim_fraction: float = 0.35
) -> np.ndarray:
    """Trim uniformly-dark scanner borders/black edges from each side of the page.

    Walks inward from each edge while that row/column's mean intensity is
    below `dark_mean_threshold` (i.e. it looks like scanner-bed border, not
    page content). Unlike a tight content bounding box, this leaves normal
    white margins around text untouched — only actual black borders are cut.
    `max_trim_fraction` bounds how much of each dimension can be trimmed, as
    a safety net against pathological (e.g. fully black) input.
    """
    gray = to_grayscale(image)
    h, w = gray.shape
    max_trim_rows = int(h * max_trim_fraction)
    max_trim_cols = int(w * max_trim_fraction)

    top, bottom = 0, h
    left, right = 0, w

    while top < bottom - 1 and top < max_trim_rows and gray[top, left:right].mean() < dark_mean_threshold:
        top += 1
    while (
        bottom > top + 1
        and (h - bottom) < max_trim_rows
        and gray[bottom - 1, left:right].mean() < dark_mean_threshold
    ):
        bottom -= 1
    while left < right - 1 and left < max_trim_cols and gray[top:bottom, left].mean() < dark_mean_threshold:
        left += 1
    while (
        right > left + 1
        and (w - right) < max_trim_cols
        and gray[top:bottom, right - 1].mean() < dark_mean_threshold
    ):
        right -= 1

    return image[top:bottom, left:right]


def estimate_quality(image: np.ndarray) -> float:
    """Estimate a 0-1 OCR-readiness quality score from sharpness and contrast.

    Combines Laplacian variance (blur/sharpness) and pixel-intensity stddev
    (contrast), each squashed to [0, 1] and averaged. Downstream stages can
    flag documents below a threshold as "likely poor OCR" before running the
    (expensive) OCR step.
    """
    gray = to_grayscale(image)
    sharpness = cv2.Laplacian(gray, cv2.CV_64F).var()
    contrast = float(gray.std())

    # Empirically-chosen normalization ceilings for typical 300dpi document scans.
    sharpness_score = min(sharpness / 500.0, 1.0)
    contrast_score = min(contrast / 80.0, 1.0)
    return round((sharpness_score + contrast_score) / 2.0, 4)


PIPELINE_STEPS: dict[str, Callable[[np.ndarray], np.ndarray]] = {
    "deskew": deskew,
    "denoise": denoise,
    "clahe": clahe_enhance,
    "binarize": binarize,
    "auto_crop": auto_crop,
}

DEFAULT_PIPELINE: tuple[str, ...] = ("deskew", "denoise", "auto_crop", "clahe", "binarize")


@dataclass
class PreprocessResult:
    image: np.ndarray
    quality_score: float


def preprocess(
    image: np.ndarray, steps: Sequence[str] = DEFAULT_PIPELINE
) -> PreprocessResult:
    """Run the configured sequence of preprocessing steps on a scanned image.

    The quality score is computed once, up front, on the grayscale image
    before any lossy steps (denoise/binarize) — it reflects the scan's
    intrinsic sharpness/contrast, not the processed output.

    `clahe` is applied only as a fallback: it's skipped when the image
    already has adequate contrast, since histogram equalization can amplify
    noise on already-clean scans.
    """
    gray = to_grayscale(image)
    quality_score = estimate_quality(gray)

    result = gray
    for step in steps:
        if step == "clahe":
            if float(result.std()) < LOW_CONTRAST_STDDEV_THRESHOLD:
                result = clahe_enhance(result)
            continue
        if step not in PIPELINE_STEPS:
            raise ValueError(f"unknown preprocessing step: {step!r}")
        result = PIPELINE_STEPS[step](result)

    return PreprocessResult(image=result, quality_score=quality_score)
