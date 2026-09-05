from io import BytesIO

import cv2
import numpy as np
from PIL import Image

from config import get_settings


class InvalidImageError(ValueError):
    pass


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
