from io import BytesIO

import cv2
import numpy as np
from PIL import Image

from config import get_settings


class InvalidImageError(ValueError):
    """An upload the pipeline won't accept (bad image, bad PDF, too many pages)."""


async def read_upload_bounded(upload, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise InvalidImageError(f"upload exceeds the {limit}-byte limit")
        chunks.append(chunk)
    return b"".join(chunks)


def decode_and_check_image(content: bytes) -> np.ndarray:
    settings = get_settings()
    if not content:
        raise InvalidImageError("empty upload")
    # Check declared dimensions from the container header before doing a
    # full decode. Image.open() is lazy -- it reads the header only, not the
    # pixel data -- so this rejects an oversized image cheaply. Without this,
    # cv2.imdecode below allocates the entire decoded pixel buffer up front,
    # so a small, highly-compressible file (e.g. a solid-color PNG a few
    # hundred KB in size) that declares huge dimensions can force a
    # multi-gigabyte allocation before the pixel-count check ever ran -- a
    # classic decompression-bomb DoS that the file-size cap alone doesn't
    # stop.
    try:
        with Image.open(BytesIO(content)) as probe:
            width, height = probe.size
    except Exception as exc:
        raise InvalidImageError("not a readable image") from exc
    if width * height > settings.max_image_pixels:
        raise InvalidImageError(
            f"image dimensions {width}x{height} exceed the {settings.max_image_pixels}-pixel limit"
        )
    decoded = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if decoded is None:
        raise InvalidImageError("not a readable image")
    return decoded


def _check_pdf(content: bytes, max_pages: int) -> int:
    import pypdfium2 as pdfium

    from ocr.pages import PDFIUM_LOCK

    with PDFIUM_LOCK:
        try:
            pdf = pdfium.PdfDocument(content)
        except pdfium.PdfiumError as exc:
            # Also covers password-protected PDFs, which can't be rendered.
            raise InvalidImageError(f"not a readable PDF ({exc})") from exc
        try:
            page_count = len(pdf)
        finally:
            pdf.close()
    if page_count < 1:
        raise InvalidImageError("PDF has no pages")
    if page_count > max_pages:
        raise InvalidImageError(f"PDF has {page_count} pages, over the {max_pages}-page limit")
    # No per-page pixel check needed here: ocr.pages renders each page at a
    # scale clamped to max_image_pixels, so a huge page can't force a huge
    # bitmap allocation.
    return page_count


def _check_multiframe_image(content: bytes, max_pages: int, max_pixels: int) -> int:
    try:
        with Image.open(BytesIO(content)) as probe:
            frame_count = int(getattr(probe, "n_frames", 1))
            if frame_count > max_pages:
                raise InvalidImageError(
                    f"image has {frame_count} pages/frames, over the {max_pages}-page limit"
                )
            # Header-only check of every frame, not just the first: a
            # multi-page TIFF can hide a decompression bomb on page 2.
            for index in range(frame_count):
                probe.seek(index)
                width, height = probe.size
                if width * height > max_pixels:
                    raise InvalidImageError(
                        f"page {index + 1} dimensions {width}x{height} exceed the "
                        f"{max_pixels}-pixel limit"
                    )
    except InvalidImageError:
        raise
    except Exception as exc:
        raise InvalidImageError("not a readable image") from exc
    return frame_count


def check_document(content: bytes) -> int:
    """Validate an uploaded scan (image, multi-page TIFF or PDF) and return
    its page count. Raises InvalidImageError on anything the pipeline
    shouldn't accept: unreadable files, too many pages, or any page whose
    declared dimensions exceed the pixel cap."""
    from ocr.pages import is_pdf

    settings = get_settings()
    if not content:
        raise InvalidImageError("empty upload")
    if is_pdf(content[:1024]):
        return _check_pdf(content, settings.max_pages_per_document)
    frame_count = _check_multiframe_image(
        content, settings.max_pages_per_document, settings.max_image_pixels
    )
    # Full decode of the first page as before, so a truncated/corrupt file
    # is still rejected at upload rather than failing later in the worker.
    decode_and_check_image(content)
    return frame_count
