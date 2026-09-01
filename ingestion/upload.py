"""Handles receiving raw scanned images and persisting them to the configured
storage backend (local disk or S3). Preprocessing/OCR dispatch is wired in
later stages.
"""

from pathlib import Path
from uuid import UUID

from config import get_settings


def save_raw_image(document_id: UUID, filename: str, content: bytes) -> str:
    """Persist a raw uploaded image and return its storage path.

    Stub: writes to local disk under storage_local_path. S3 support is added
    when the storage backend is implemented.
    """
    settings = get_settings()
    if settings.storage_backend != "local":
        raise NotImplementedError(f"storage backend '{settings.storage_backend}' not implemented yet")

    dest_dir = Path(settings.storage_local_path)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"{document_id}_{filename}"
    dest_path.write_bytes(content)
    return str(dest_path)
