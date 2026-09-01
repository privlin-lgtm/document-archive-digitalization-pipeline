"""Handles receiving raw scanned images and persisting them to the configured
storage backend (local disk or S3). Preprocessing/OCR dispatch is wired in
later stages.
"""

import logging
import re
from pathlib import Path
from uuid import UUID

from config import get_settings

logger = logging.getLogger(__name__)

# Only allow a conservative filename character set; anything else (path
# separators, null bytes, leading dots, etc.) is stripped or rejected before
# it ever touches a filesystem path.
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]")


class UnsafeFilenameError(ValueError):
    pass


class UploadTooLargeError(ValueError):
    pass


def sanitize_filename(filename: str) -> str:
    """Reduce a client-supplied filename to a safe basename.

    Client input must never be trusted to build a filesystem path directly:
    a filename like "../../etc/cron.d/evil" concatenated into a path can
    escape the intended storage directory. `Path(...).name` strips any
    directory components, and the character allowlist rejects anything else
    that could be used to smuggle a path separator back in.
    """
    basename = Path(filename).name
    if not basename or basename in (".", ".."):
        raise UnsafeFilenameError(f"invalid filename: {filename!r}")
    safe = _SAFE_FILENAME_RE.sub("_", basename)
    if not safe.strip("_."):
        raise UnsafeFilenameError(f"filename has no safe characters: {filename!r}")
    return safe


def save_raw_image(document_id: UUID, filename: str, content: bytes) -> str:
    """Persist a raw uploaded image and return its storage path.

    Stub: writes to local disk under storage_local_path. S3 support is added
    when the storage backend is implemented.
    """
    settings = get_settings()
    if settings.storage_backend != "local":
        raise NotImplementedError(f"storage backend '{settings.storage_backend}' not implemented yet")

    if len(content) > settings.max_upload_size_bytes:
        raise UploadTooLargeError(
            f"upload of {len(content)} bytes exceeds the "
            f"{settings.max_upload_size_bytes}-byte limit"
        )

    safe_filename = sanitize_filename(filename)

    dest_dir = Path(settings.storage_local_path)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"{document_id}_{safe_filename}"
    dest_path.write_bytes(content)
    logger.info("saved raw upload for document %s to %s (%d bytes)", document_id, dest_path, len(content))
    return str(dest_path)
