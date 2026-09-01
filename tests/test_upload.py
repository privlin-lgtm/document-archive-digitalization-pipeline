import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from ingestion.upload import (
    UnsafeFilenameError,
    UploadTooLargeError,
    sanitize_filename,
    save_raw_image,
)


def fake_settings(**overrides) -> SimpleNamespace:
    base = dict(storage_backend="local", storage_local_path="", max_upload_size_bytes=50 * 1024 * 1024)
    base.update(overrides)
    return SimpleNamespace(**base)


class TestSanitizeFilename:
    def test_strips_path_traversal_directories(self):
        assert sanitize_filename("../../etc/passwd") == "passwd"

    def test_strips_embedded_directories(self):
        assert sanitize_filename("a/b/c.png") == "c.png"

    def test_strips_windows_style_directories(self):
        assert sanitize_filename("..\\..\\windows\\evil.exe") == "evil.exe"

    def test_replaces_unsafe_characters(self):
        assert sanitize_filename("weird name!.png") == "weird_name_.png"

    def test_rejects_traversal_only_input(self):
        with pytest.raises(UnsafeFilenameError):
            sanitize_filename("../")

    def test_rejects_dot_only(self):
        with pytest.raises(UnsafeFilenameError):
            sanitize_filename(".")


class TestSaveRawImage:
    def test_writes_file_under_storage_root(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "ingestion.upload.get_settings", lambda: fake_settings(storage_local_path=str(tmp_path))
        )
        document_id = uuid.uuid4()

        path = save_raw_image(document_id, "scan.png", b"fake-image-bytes")

        assert Path(path).exists()
        assert Path(path).parent == tmp_path
        assert Path(path).read_bytes() == b"fake-image-bytes"

    def test_path_traversal_filename_cannot_escape_storage_root(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "ingestion.upload.get_settings", lambda: fake_settings(storage_local_path=str(tmp_path))
        )

        path = save_raw_image(uuid.uuid4(), "../../../evil.png", b"x")

        # The traversal is neutralized (only the basename survives) rather
        # than rejected outright; either way, the write must land inside
        # the configured storage root, never above it.
        assert Path(path).parent == tmp_path
        assert not (tmp_path.parent.parent / "evil.png").exists()

    def test_rejects_oversized_upload(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "ingestion.upload.get_settings",
            lambda: fake_settings(storage_local_path=str(tmp_path), max_upload_size_bytes=10),
        )

        with pytest.raises(UploadTooLargeError):
            save_raw_image(uuid.uuid4(), "big.png", b"x" * 100)

        assert list(tmp_path.iterdir()) == []

    def test_s3_backend_not_yet_implemented(self, monkeypatch):
        monkeypatch.setattr("ingestion.upload.get_settings", lambda: fake_settings(storage_backend="s3"))

        with pytest.raises(NotImplementedError):
            save_raw_image(uuid.uuid4(), "a.png", b"x")
