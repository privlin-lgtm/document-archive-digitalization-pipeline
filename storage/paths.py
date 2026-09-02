"""Filesystem helpers that keep stored paths inside the configured root."""

from pathlib import Path

from config import get_settings


class UnsafeStoragePath(ValueError):
    pass


def storage_root() -> Path:
    return Path(get_settings().storage_local_path).resolve()


def confined_path(stored_path: str) -> Path:
    """Resolve `stored_path` and reject anything outside STORAGE_LOCAL_PATH."""
    root = storage_root()
    resolved = Path(stored_path).resolve()
    if not resolved.is_relative_to(root):
        raise UnsafeStoragePath(f"{resolved} is outside storage root {root}")
    return resolved


def processed_image_dest(document_id, page_number: int = 1) -> Path:
    dest_dir = storage_root()
    dest_dir.mkdir(parents=True, exist_ok=True)
    return dest_dir / f"{document_id}_p{page_number}_processed.png"
