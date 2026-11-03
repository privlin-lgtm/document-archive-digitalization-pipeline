"""Reads one page of a drop-in scan for the calibration harness.

Pages come from ocr.pages.iter_page_images, the same splitter the pipeline
uses, so a PDF page or TIFF frame is rendered exactly as it would be in
production (PDF_RENDER_DPI, MAX_IMAGE_PIXELS). There is no second renderer
here.

iter_page_images yields pages in order, so `PageReader` keeps the current
scan's iterator open: asking for pages of one file in ascending order
renders each page once. Callers sort their requests by (file, page).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Self

import cv2
import numpy as np


class PageReader:
    def __init__(self) -> None:
        self._path: Path | None = None
        self._pages: Iterator[np.ndarray] | None = None
        self._next = 1  # page number the iterator yields next
        self._last: tuple[int, np.ndarray] | None = None

    def read(self, path: Path, page: int | None) -> np.ndarray:
        """BGR image of `page` (1-based) of `path`. `page=None` means a single
        image, read with cv2.imread exactly as before multi-page support.
        Raises IndexError if the scan has fewer pages, ValueError if the file
        can't be read."""
        if page is None:
            image = cv2.imread(str(path))
            if image is None:
                raise ValueError(f"could not read image: {path}")
            return image

        if self._path == path and self._last is not None and self._last[0] == page:
            return self._last[1]
        if self._path != path or self._pages is None or page < self._next:
            self.close()
            from ocr.pages import iter_page_images

            self._path = path
            self._pages = iter_page_images(str(path))
            self._next = 1
        for image in self._pages:
            number = self._next
            self._next += 1
            if number == page:
                self._last = (number, image)
                return image
        self.close()
        raise IndexError(f"{path.name} has no page {page}")

    def close(self) -> None:
        """Releases the open scan (closes the PDF, if it was one)."""
        close = getattr(self._pages, "close", None)
        if close is not None:
            close()
        self._path = None
        self._pages = None
        self._last = None
        self._next = 1

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
