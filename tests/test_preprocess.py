import cv2
import numpy as np

from ocr.preprocess import (
    auto_crop,
    binarize,
    clahe_enhance,
    denoise,
    deskew,
    estimate_quality,
    estimate_skew_angle,
    preprocess,
)


def make_text_page(width: int = 600, height: int = 400) -> np.ndarray:
    """A clean synthetic page: black horizontal text lines on a white background."""
    page = np.full((height, width), 255, dtype=np.uint8)
    for y in range(40, height - 40, 30):
        cv2.putText(
            page,
            "Document Archive 1897",
            (30, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            0,
            2,
            cv2.LINE_AA,
        )
    return page


def rotate_image(image: np.ndarray, angle: float) -> np.ndarray:
    h, w = image.shape[:2]
    center = (w / 2, h / 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(image, matrix, (w, h), flags=cv2.INTER_CUBIC, borderValue=255)


def add_gaussian_noise(image: np.ndarray, sigma: float = 25.0) -> np.ndarray:
    noise = np.random.default_rng(0).normal(0, sigma, image.shape)
    noisy = image.astype(np.float64) + noise
    return np.clip(noisy, 0, 255).astype(np.uint8)


def fade(image: np.ndarray, low: int = 110, high: int = 160) -> np.ndarray:
    """Squash the dynamic range to simulate a faded/low-contrast scan."""
    normalized = image.astype(np.float64) / 255.0
    faded = low + normalized * (high - low)
    return faded.astype(np.uint8)


def add_black_border(image: np.ndarray, border: int = 30) -> np.ndarray:
    return cv2.copyMakeBorder(
        image, border, border, border, border, cv2.BORDER_CONSTANT, value=0
    )


class TestDeskew:
    def test_estimate_skew_angle_recovers_known_rotation(self):
        page = make_text_page()
        rotated = rotate_image(page, angle=8.0)
        # rotate_image rotates by `angle`; the correction needed is -angle.
        estimated = estimate_skew_angle(rotated)
        assert abs(estimated - (-8.0)) <= 1.0

    def test_deskew_reduces_residual_skew(self):
        page = make_text_page()
        rotated = rotate_image(page, angle=-6.0)
        corrected = deskew(rotated)
        residual_before = abs(estimate_skew_angle(rotated))
        residual_after = abs(estimate_skew_angle(corrected))
        assert residual_after < residual_before

    def test_deskew_is_noop_on_already_straight_page(self):
        page = make_text_page()
        corrected = deskew(page)
        assert corrected.shape == page.shape


class TestDenoise:
    def test_denoise_moves_image_closer_to_clean_original(self):
        page = make_text_page()
        noisy = add_gaussian_noise(page, sigma=30.0)
        denoised = denoise(noisy)

        error_before = np.mean((noisy.astype(np.float64) - page.astype(np.float64)) ** 2)
        error_after = np.mean((denoised.astype(np.float64) - page.astype(np.float64)) ** 2)
        assert error_after < error_before

    def test_denoise_preserves_shape_and_dtype(self):
        page = make_text_page()
        denoised = denoise(page)
        assert denoised.shape == page.shape
        assert denoised.dtype == page.dtype


class TestContrast:
    def test_estimate_quality_flags_low_contrast_page_lower(self):
        page = make_text_page()
        faded = fade(page)
        assert estimate_quality(faded) < estimate_quality(page)

    def test_clahe_enhance_increases_contrast_on_faded_page(self):
        page = make_text_page()
        faded = fade(page)
        enhanced = clahe_enhance(faded)
        assert float(enhanced.std()) > float(faded.std())

    def test_binarize_produces_binary_image(self):
        page = make_text_page()
        binary = binarize(page)
        unique_values = set(np.unique(binary).tolist())
        assert unique_values <= {0, 255}


class TestAutoCrop:
    def test_auto_crop_removes_black_scanner_border(self):
        page = make_text_page()
        bordered = add_black_border(page, border=30)
        cropped = auto_crop(bordered)
        assert cropped.shape[0] < bordered.shape[0]
        assert cropped.shape[1] < bordered.shape[1]
        # No near-black scanner-border pixels should remain at the very edges.
        assert cropped[0, :].min() > 10 or cropped[0, :].mean() > 10
        assert cropped[:, 0].min() > 10 or cropped[:, 0].mean() > 10

    def test_auto_crop_noop_when_no_border(self):
        page = make_text_page()
        cropped = auto_crop(page)
        assert cropped.shape == page.shape


class TestPreprocessPipeline:
    def test_full_pipeline_on_degraded_synthetic_scan(self):
        page = make_text_page()
        degraded = rotate_image(page, angle=5.0)
        degraded = add_gaussian_noise(degraded, sigma=15.0)
        degraded = fade(degraded)
        degraded = add_black_border(degraded, border=20)

        result = preprocess(degraded)

        assert result.image.dtype == np.uint8
        assert result.image.ndim == 2
        assert 0.0 <= result.quality_score <= 1.0
        # Border should be gone (auto_crop ran), so the image is smaller than the input.
        assert result.image.shape[0] < degraded.shape[0]
        assert result.image.shape[1] < degraded.shape[1]

    def test_steps_can_be_toggled(self):
        page = make_text_page()
        result = preprocess(page, steps=("binarize",))
        unique_values = set(np.unique(result.image).tolist())
        assert unique_values <= {0, 255}

    def test_unknown_step_raises(self):
        page = make_text_page()
        try:
            preprocess(page, steps=("not_a_real_step",))
            assert False, "expected ValueError"
        except ValueError:
            pass
