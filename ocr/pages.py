"""Load one or more page images from a stored scan (including multi-page TIFF)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageSequence


def load_page_images(path: str) -> list[np.ndarray]:
    """Return BGR arrays, one per page. Single-frame files yield one image."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(path)

    try:
        with Image.open(source) as pil:
            frames = getattr(pil, "n_frames", 1)
            if frames <= 1:
                image = cv2.imread(str(source))
                return [image] if image is not None else []
            pages: list[np.ndarray] = []
            for frame in ImageSequence.Iterator(pil):
                rgb = np.array(frame.convert("RGB"))
                pages.append(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
            return pages
    except (OSError, ValueError):
        image = cv2.imread(str(source))
        return [image] if image is not None else []
