import enum
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import (
    Computed,
    Date,
    Enum,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from storage.db import Base


class DocumentStatus(str, enum.Enum):
    uploaded = "uploaded"
    preprocessing = "preprocessing"
    ocr_running = "ocr_running"
    ocr_done = "ocr_done"
    ocr_partial = "ocr_partial"
    extracting = "extracting"
    indexed = "indexed"
    needs_review = "needs_review"
    ready = "ready"
    enqueue_failed = "enqueue_failed"
    error = "error"


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    upload_time: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC), nullable=False
    )
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status"),
        default=DocumentStatus.uploaded,
        nullable=False,
    )
    raw_image_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    processed_image_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    pages: Mapped[list["Page"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    review_flags: Mapped[list["ReviewFlag"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Page(Base):
    """One page of a (possibly multi-page) document.

    `full_text` is the concatenation of this page's regions' OCR text,
    populated once OCR has run for every region on the page.
    `full_text_search` is a stored generated tsvector column (see the
    Alembic migration for the GIN index) — full-text search runs against
    this table, at page granularity, per the stage 5 spec.
    """

    __tablename__ = "pages"
    __table_args__ = (UniqueConstraint("document_id", "page_number", name="uq_pages_document_page_number"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    full_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    full_text_search: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(full_text, ''))", persisted=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    processed_image_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    document: Mapped[Document] = relationship(back_populates="pages")
    regions: Mapped[list["Region"]] = relationship(back_populates="page", cascade="all, delete-orphan")


class RegionType(str, enum.Enum):
    """Mirrors ocr.layout.RegionType."""

    paragraph = "paragraph"
    table = "table"
    signature = "signature"
    margin_annotation = "margin_annotation"
    stamp = "stamp"


class Region(Base):
    """A layout region on a page (see ocr.layout.Region). The bbox is stored
    as four columns rather than a JSON/array blob so it stays trivially
    queryable/indexable if position-based lookups are needed later.
    """

    __tablename__ = "regions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"), nullable=False)
    bbox_x: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_y: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_w: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_h: Mapped[int] = mapped_column(Integer, nullable=False)
    region_type: Mapped[RegionType] = mapped_column(Enum(RegionType, name="region_type"), nullable=False)
    reading_order: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    page: Mapped[Page] = relationship(back_populates="regions")
    ocr_result: Mapped["OCRResultRecord | None"] = relationship(
        back_populates="region", cascade="all, delete-orphan", uselist=False
    )
    entities: Mapped[list["Entity"]] = relationship(back_populates="region", cascade="all, delete-orphan")


class OCRStatus(str, enum.Enum):
    """Mirrors ocr.engine.OCRStatus."""

    ok = "ok"
    ocr_partial = "ocr_partial"
    failed = "failed"


class OCRResultRecord(Base):
    """Persisted form of ocr.engine.OCRResult for one region. One-to-one
    with Region — OCR runs per region in this pipeline (see ocr.layout then
    ocr.engine). Named *Record* to avoid confusion with the ocr.engine
    dataclass of (almost) the same name.
    """

    __tablename__ = "ocr_results"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    region_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("regions.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    engine: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)  # OCRResult.document_confidence
    status: Mapped[OCRStatus] = mapped_column(Enum(OCRStatus, name="ocr_result_status"), nullable=False)
    notes: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    region: Mapped[Region] = relationship(back_populates="ocr_result")


class EntityType(str, enum.Enum):
    """Mirrors extraction.entities.EntityType."""

    person = "person"
    date = "date"
    location = "location"
    amount = "amount"


class Entity(Base):
    """Persisted form of extraction.entities.ExtractedEntity.

    `date_value`/`amount_value`+`amount_currency` are typed, queryable
    projections of `normalized_value` for the entity types that have one —
    populated only for the matching entity_type, NULL otherwise. This is
    what lets range queries ("amounts over $X", "dates between Y and Z")
    use a real index instead of parsing `normalized_value` at query time.
    """

    __tablename__ = "entities"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    region_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("regions.id", ondelete="CASCADE"), nullable=False)
    entity_type: Mapped[EntityType] = mapped_column(Enum(EntityType, name="entity_type"), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    start_char: Mapped[int] = mapped_column(Integer, nullable=False)
    end_char: Mapped[int] = mapped_column(Integer, nullable=False)

    # entity_type == "date" only.
    date_value: Mapped[date | None] = mapped_column(Date, nullable=True)
    # entity_type == "amount" only.
    amount_value: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    amount_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    region: Mapped[Region] = relationship(back_populates="entities")
    review_flags: Mapped[list["ReviewFlag"]] = relationship(
        back_populates="entity", cascade="all, delete-orphan"
    )
    corrections: Mapped[list["EntityCorrection"]] = relationship(
        back_populates="entity", cascade="all, delete-orphan", order_by="EntityCorrection.created_at"
    )


class EntityCorrection(Base):
    """A human correction of an entity's value (PATCH /entities/{id}).

    `entity.normalized_value` is updated to the corrected value so
    downstream queries/search see the current best value, but
    `entity.raw_text` (the original OCR output) is never touched — and
    every correction is appended here rather than overwriting a single
    "corrected value" column, preserving a full audit trail of who changed
    what, and from what, over time.
    """

    __tablename__ = "entity_corrections"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    original_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_value: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    entity: Mapped[Entity] = relationship(back_populates="corrections")


class FlagSeverity(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"


class FlagType(str, enum.Enum):
    """The flag types stage 6 (extraction/anomalies.py) will raise. Defined
    now so this migration doesn't need a follow-up enum-value migration.
    """

    low_ocr_confidence = "low_ocr_confidence"
    illegible = "illegible"
    entity_conflict = "entity_conflict"
    extraction_failure = "extraction_failure"


class ReviewFlagStatus(str, enum.Enum):
    open = "open"
    resolved = "resolved"
    dismissed = "dismissed"


class ReviewFlag(Base):
    __tablename__ = "review_flags"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    page_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"), nullable=True)
    region_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("regions.id", ondelete="CASCADE"), nullable=True
    )
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), nullable=True
    )
    flag_type: Mapped[FlagType] = mapped_column(Enum(FlagType, name="review_flag_type"), nullable=False)
    severity: Mapped[FlagSeverity] = mapped_column(Enum(FlagSeverity, name="review_flag_severity"), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ReviewFlagStatus] = mapped_column(
        Enum(ReviewFlagStatus, name="review_flag_status"),
        nullable=False,
        default=ReviewFlagStatus.open,
    )
    status_changed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    document: Mapped[Document] = relationship(back_populates="review_flags")
    entity: Mapped[Entity | None] = relationship(back_populates="review_flags")
