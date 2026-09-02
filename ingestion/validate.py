import cv2
import numpy as np

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
    decoded = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if decoded is None:
        raise InvalidImageError("not a readable image")
    height, width = decoded.shape[:2]
    if width * height > settings.max_image_pixels:
        raise InvalidImageError(
            f"image dimensions {width}x{height} exceed the {settings.max_image_pixels}-pixel limit"
        )
    return decoded
