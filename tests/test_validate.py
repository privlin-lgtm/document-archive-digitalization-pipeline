"""Tests for ingestion/validate.py's decode_and_check_image.

Previously untested (see git history): the max_image_pixels guard existed
but nothing asserted it actually rejects an oversized image, or that it does
so *before* a full pixel decode -- see decode_and_check_image's docstring
comment for why that ordering matters (a small, highly-compressible file
declaring huge dimensions would otherwise force a large allocation in
cv2.imdecode before the pixel-count check ever ran).
"""

import struct
import zlib
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from ingestion import validate


def fake_settings(**overrides) -> SimpleNamespace:
    base = {"max_image_pixels": 40_000_000}
    base.update(overrides)
    return SimpleNamespace(**base)


def make_png_bytes(width: int = 20, height: int = 20) -> bytes:
    image = np.full((height, width), 255, dtype=np.uint8)
    ok, buffer = cv2.imencode(".png", image)
    assert ok
    return buffer.tobytes()


def crafted_oversized_png_bytes(width: int, height: int) -> bytes:
    """A ~65-byte PNG whose IHDR declares `width`x`height` with no real pixel
    data behind it -- large enough to prove the size check runs off the
    header alone, not off the decoded image."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    idat = zlib.compress(b"")
    return signature + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


class TestDecodeAndCheckImage:
    def test_valid_image_decodes(self, monkeypatch):
        monkeypatch.setattr(validate, "get_settings", lambda: fake_settings())
        decoded = validate.decode_and_check_image(make_png_bytes(20, 20))
        assert decoded.shape[:2] == (20, 20)

    def test_empty_upload_rejected(self, monkeypatch):
        monkeypatch.setattr(validate, "get_settings", lambda: fake_settings())
        with pytest.raises(validate.InvalidImageError, match="empty upload"):
            validate.decode_and_check_image(b"")

    def test_garbage_bytes_rejected(self, monkeypatch):
        monkeypatch.setattr(validate, "get_settings", lambda: fake_settings())
        with pytest.raises(validate.InvalidImageError, match="not a readable image"):
            validate.decode_and_check_image(b"this is not an image")

    def test_oversized_declared_dimensions_rejected_before_full_decode(self, monkeypatch):
        # Declared 10000x10000 = 100M pixels, ~65 real bytes on the wire --
        # above the app's own 40M-pixel limit but below Pillow's built-in
        # decompression-bomb hard error threshold (~178M), so this exercises
        # *this app's* configured limit specifically, not Pillow's default.
        # If this were decoded first, cv2 would try to allocate a 100MB+
        # buffer for a file this small.
        monkeypatch.setattr(validate, "get_settings", lambda: fake_settings(max_image_pixels=40_000_000))
        oversized = crafted_oversized_png_bytes(10_000, 10_000)
        with pytest.raises(validate.InvalidImageError, match="exceed the 40000000-pixel limit"):
            validate.decode_and_check_image(oversized)
