"""Turn one stored scan into page images: single images, multi-frame TIFFs
and multi-page PDFs.

PDFs are rasterised with pypdfium2 (Google's PDFium engine, as used by
Chrome). It was chosen over the other common options for licensing and
packaging: pypdfium2 is Apache-2.0/BSD-3-Clause and PDFium itself is
BSD-3-Clause, so it is safe to ship in a commercial product, whereas
PyMuPDF/MuPDF are AGPL and pdf2image needs the GPL poppler binaries
installed separately. pypdfium2 ships self-contained manylinux/macOS/
Windows wheels, so the Docker image needs no extra system packages.

Pages are yielded one at a time (`iter_page_images`) so a 200-page PDF
never holds 200 rendered 300-DPI bitmaps in memory at once.
"""

from __future__ import annotations

import logging
import math
import threading
from collections.abc import Iterator
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageSequence

logger = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF-"

# Points per inch -- PDF page sizes are expressed in points.
_POINTS_PER_INCH = 72.0

# PDFium is not thread-safe: two threads touching any PDFium object at the
# same time can crash the process. Batch ingestion validates files from a
# thread pool, so every PDFium call in this process goes through this lock.
PDFIUM_LOCK = threading.Lock()


def is_pdf(head: bytes) -> bool:
    """True if `head` (the first bytes of a file) looks like a PDF. Some
    writers put a little junk before the header, so look in the first 1 KB
    the same way PDF readers do."""
    return PDF_MAGIC in head[:1024]


def _file_is_pdf(path: Path) -> bool:
    with path.open("rb") as handle:
        return is_pdf(handle.read(1024))


def pdf_render_scale(width_pt: float, height_pt: float, dpi: int, max_pixels: int) -> float:
    """The pdfium render scale (pixels per point) for one page: `dpi`, unless
    that would exceed `max_pixels`, in which case the largest scale that fits.
    An oversized page (a map, a poster-size plan) is rendered at a lower DPI
    rather than rejected or allowed to allocate a huge bitmap."""
    scale = dpi / _POINTS_PER_INCH
    area_pt = max(width_pt, 1.0) * max(height_pt, 1.0)
    if area_pt * scale * scale > max_pixels:
        scale = math.sqrt(max_pixels / area_pt)
    return scale


def count_pages(path: str) -> int:
    """Number of pages in a stored scan without rendering any of them."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(path)
    if _file_is_pdf(source):
        import pypdfium2 as pdfium

        with PDFIUM_LOCK:
            pdf = pdfium.PdfDocument(str(source))
            try:
                return len(pdf)
            finally:
                pdf.close()
    try:
        with Image.open(source) as pil:
            return int(getattr(pil, "n_frames", 1))
    except (OSError, ValueError):
        return 1 if cv2.imread(str(source)) is not None else 0


def _iter_pdf_pages(source: Path, dpi: int, max_pixels: int) -> Iterator[np.ndarray]:
    import pypdfium2 as pdfium

    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(str(source))
        page_count = len(pdf)
    try:
        for index in range(page_count):
            with PDFIUM_LOCK:
                page = pdf[index]
                try:
                    width_pt, height_pt = page.get_size()
                    scale = pdf_render_scale(width_pt, height_pt, dpi, max_pixels)
                    if scale < dpi / _POINTS_PER_INCH:
                        logger.warning(
                            "pdf %s page %d is %.0fx%.0fpt; rendering at %.0f DPI instead of %d "
                            "to stay under the %d-pixel limit",
                            source.name, index + 1, width_pt, height_pt,
                            scale * _POINTS_PER_INCH, dpi, max_pixels,
                        )
                    bitmap = page.render(scale=scale)
                    try:
                        rgb = np.array(bitmap.to_pil().convert("RGB"))
                    finally:
                        bitmap.close()
                finally:
                    page.close()
            yield cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    finally:
        with PDFIUM_LOCK:
            pdf.close()


def _iter_raster_pages(source: Path) -> Iterator[np.ndarray]:
    try:
        with Image.open(source) as probe:
            frames = int(getattr(probe, "n_frames", 1))
    except (OSError, ValueError):
        frames = 1
    if frames > 1:
        with Image.open(source) as pil:
            for frame in ImageSequence.Iterator(pil):
                rgb = np.array(frame.convert("RGB"))
                yield cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        return
    # Single-frame images keep going through cv2.imread, exactly as before
    # multi-page support existed, so single-image behaviour is unchanged.
    image = cv2.imread(str(source))
    if image is not None:
        yield image


def iter_page_images(
    path: str, *, dpi: int | None = None, max_pixels: int | None = None
) -> Iterator[np.ndarray]:
    """Yield BGR arrays, one per page, in page order. Single-frame images
    yield one page; multi-frame TIFFs one per frame; PDFs one per page,
    rendered at `dpi` (default: settings.pdf_render_dpi)."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(path)
    if _file_is_pdf(source):
        if dpi is None or max_pixels is None:
            from config import get_settings

            settings = get_settings()
            dpi = settings.pdf_render_dpi if dpi is None else dpi
            max_pixels = settings.max_image_pixels if max_pixels is None else max_pixels
        yield from _iter_pdf_pages(source, dpi, max_pixels)
    else:
        yield from _iter_raster_pages(source)


def load_page_images(path: str) -> list[np.ndarray]:
    """Return every page at once. Prefer `iter_page_images` for anything that
    may be a long PDF."""
    return list(iter_page_images(path))


def is_pdf_path(path: str) -> bool:
    """True if the file at `path` is a PDF (False if it's missing)."""
    source = Path(path)
    return source.is_file() and _file_is_pdf(source)
