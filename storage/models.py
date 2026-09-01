import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from storage.db import Base


class DocumentStatus(str, enum.Enum):
    uploaded = "uploaded"
    preprocessing = "preprocessing"
    ocr_running = "ocr_running"
    ocr_done = "ocr_done"
    extracting = "extracting"
    indexed = "indexed"
    needs_review = "needs_review"
    error = "error"


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    upload_time: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc), nullable=False
    )
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status"),
        default=DocumentStatus.uploaded,
        nullable=False,
    )
    raw_image_path: Mapped[str] = mapped_column(String(1024), nullable=False)
