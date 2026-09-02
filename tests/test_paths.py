from pathlib import Path

import pytest

from storage.paths import UnsafeStoragePath, confined_path


def test_confined_path_accepts_a_file_under_storage_root(tmp_path, monkeypatch):
    monkeypatch.setattr("storage.paths.get_settings", lambda: type("S", (), {"storage_local_path": str(tmp_path)})())
    target = tmp_path / "scan.png"
    target.write_bytes(b"x")
    assert confined_path(str(target)) == target.resolve()


def test_confined_path_rejects_escape(tmp_path, monkeypatch):
    monkeypatch.setattr("storage.paths.get_settings", lambda: type("S", (), {"storage_local_path": str(tmp_path)})())
    with pytest.raises(UnsafeStoragePath):
        confined_path(str(Path("/etc/passwd")))
