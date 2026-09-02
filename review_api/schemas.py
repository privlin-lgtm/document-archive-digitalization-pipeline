"""Pydantic request/response schemas for review_api."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class DocumentCreated(BaseModel):
    """One uploaded document, as returned by POST /documents."""

    id: UUID
    filename: str
    status: str = Field(description="uploaded, or enqueue_failed if the broker rejected the job")
    enqueued: bool = True


class DocumentSummary(BaseModel):
    """A single row in the GET /documents list."""

    id: UUID
    filename: str
    upload_time: datetime
    status: str


class DocumentListResponse(BaseModel):
    results: list[DocumentSummary]
    limit: int
    offset: int
    total: int


class OCRResultOut(BaseModel):
    engine: str
    text: str
    confidence: float = Field(description="0-100")
    status: str


class EntityOut(BaseModel):
    id: UUID
    entity_type: str
    raw_text: str
    normalized_value: str | None
    confidence: float = Field(description="0-1")
    corrected: bool = Field(description="Whether a human has ever corrected this entity's value")


class RegionOut(BaseModel):
    id: UUID
    bbox_x: int
    bbox_y: int
    bbox_w: int
    bbox_h: int
    region_type: str
    reading_order: int
    confidence: float
    ocr_result: OCRResultOut | None
    entities: list[EntityOut]


class PageOut(BaseModel):
    id: UUID
    page_number: int
    regions: list[RegionOut]


class ReviewFlagOut(BaseModel):
    id: UUID
    flag_type: str
    severity: str
    explanation: str
    status: str
    page_id: UUID | None
    region_id: UUID | None
    entity_id: UUID | None
    created_at: datetime
    status_changed_at: datetime | None


class DocumentDetail(BaseModel):
    """Full document detail: status, OCR text, regions, entities, flags."""

    id: UUID
    filename: str
    upload_time: datetime
    status: str
    error_message: str | None = None
    pages: list[PageOut]
    flags: list[ReviewFlagOut]


class SearchResultItem(BaseModel):
    page_id: UUID
    document_id: UUID
    filename: str
    page_number: int
    rank: float
    snippet: str = Field(description="ts_headline snippet with <b> tags around matched terms")


class SearchResponse(BaseModel):
    results: list[SearchResultItem]
    limit: int
    offset: int
    total: int


class EntityCorrectionRequest(BaseModel):
    corrected_value: str = Field(min_length=1, max_length=2000, description="The reviewer's corrected value")
    reviewer: str | None = Field(
        default=None,
        description="Ignored — reviewer is taken from the authenticated session",
    )


class EntityCorrectionOut(BaseModel):
    entity_id: UUID
    original_value: str | None = Field(description="The value being replaced (the prior OCR/extraction output)")
    corrected_value: str
    reviewer: str
    corrected_at: datetime


class ReviewFlagUpdateRequest(BaseModel):
    status: Literal["resolved", "dismissed"] = Field(
        description="Target status — reopening a flag isn't done through this endpoint"
    )


class ReviewFlagWithDocument(ReviewFlagOut):
    """A review flag plus enough document context for a queue view to link
    to it without a second request per row.
    """

    document_id: UUID
    document_filename: str


class ReviewFlagListResponse(BaseModel):
    results: list[ReviewFlagWithDocument]
    limit: int
    offset: int
    total: int


class StatsResponse(BaseModel):
    total_documents: int
    documents_indexed: int
    documents_needing_review: int
    open_review_flags: int
    average_ocr_confidence: float | None = Field(description="0-100, across all ocr_results")
    average_entity_confidence: float | None = Field(description="0-1, across all entities")
    open_flags_by_type: dict[str, int]
