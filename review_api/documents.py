import io
import logging
import uuid
from pathlib import Path
from uuid import UUID

import cv2
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ingestion.upload import UnsafeFilenameError, UploadTooLargeError, save_raw_image
from review_api.auth import require_api_token
from review_api.schemas import (
    DocumentCreated,
    DocumentDetail,
    DocumentListResponse,
    DocumentSummary,
    EntityOut,
    OCRResultOut,
    PageOut,
    RegionOut,
    ReviewFlagOut,
)
from storage.db import get_db
from storage.models import Document, DocumentStatus, Page, Region
from worker import run_ocr_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"], dependencies=[Depends(require_api_token)])

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 500

# BGR, one per ocr.layout.RegionType, for the annotated-image endpoint.
_REGION_TYPE_COLORS: dict[str, tuple[int, int, int]] = {
    "paragraph": (0, 200, 0),
    "table": (200, 0, 0),
    "signature": (0, 140, 255),
    "margin_annotation": (200, 0, 200),
    "stamp": (0, 0, 220),
}
_DEFAULT_REGION_COLOR = (128, 128, 128)


@router.post("", status_code=201, response_model=list[DocumentCreated])
async def upload_documents(
    files: list[UploadFile] = File(..., description="One or more scanned document images"),
    db: Session = Depends(get_db),
) -> list[DocumentCreated]:
    """Upload a scan (or batch of scans). Each file is persisted to the
    configured storage backend, given a `documents` row (status=uploaded),
    and has its OCR job enqueued. A broker hiccup while enqueuing doesn't
    fail the upload — the row is the source of truth and can be reconciled
    later — but is logged loudly since it means that document will silently
    never get processed otherwise.
    """
    created: list[Document] = []
    for upload in files:
        content = await upload.read()
        document_id = uuid.uuid4()
        try:
            raw_image_path = save_raw_image(document_id, upload.filename or "upload", content)
        except UnsafeFilenameError as exc:
            raise HTTPException(status_code=400, detail=f"invalid filename: {exc}") from exc
        except UploadTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc

        document = Document(
            id=document_id,
            filename=upload.filename or "upload",
            raw_image_path=raw_image_path,
            status=DocumentStatus.uploaded,
        )
        db.add(document)
        created.append(document)

    db.commit()

    for document in created:
        try:
            run_ocr_job.delay(str(document.id))
        except Exception:
            logger.exception("failed to enqueue OCR job for document %s", document.id)

    return [DocumentCreated(id=d.id, filename=d.filename, status=d.status.value) for d in created]


@router.get("", response_model=DocumentListResponse)
def list_documents(
    status: DocumentStatus | None = None,
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> DocumentListResponse:
    stmt = select(Document).order_by(Document.upload_time.desc()).limit(limit).offset(offset)
    if status is not None:
        stmt = stmt.where(Document.status == status)
    documents = db.scalars(stmt).all()
    return DocumentListResponse(
        results=[
            DocumentSummary(id=d.id, filename=d.filename, upload_time=d.upload_time, status=d.status.value)
            for d in documents
        ],
        limit=limit,
        offset=offset,
    )


@router.get("/{document_id}", response_model=DocumentDetail)
def get_document(document_id: UUID, db: Session = Depends(get_db)) -> DocumentDetail:
    """Full document detail: status, OCR text, regions, entities, flags."""
    document = db.scalars(
        select(Document)
        .where(Document.id == document_id)
        .options(
            selectinload(Document.pages)
            .selectinload(Page.regions)
            .selectinload(Region.ocr_result),
            selectinload(Document.pages).selectinload(Page.regions).selectinload(Region.entities),
            selectinload(Document.review_flags),
        )
    ).first()
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")

    pages = [
        PageOut(
            id=page.id,
            page_number=page.page_number,
            regions=[
                RegionOut(
                    id=region.id,
                    bbox_x=region.bbox_x,
                    bbox_y=region.bbox_y,
                    bbox_w=region.bbox_w,
                    bbox_h=region.bbox_h,
                    region_type=region.region_type.value,
                    reading_order=region.reading_order,
                    confidence=region.confidence,
                    ocr_result=(
                        OCRResultOut(
                            engine=region.ocr_result.engine,
                            text=region.ocr_result.text,
                            confidence=region.ocr_result.confidence,
                            status=region.ocr_result.status.value,
                        )
                        if region.ocr_result
                        else None
                    ),
                    entities=[
                        EntityOut(
                            id=e.id,
                            entity_type=e.entity_type.value,
                            raw_text=e.raw_text,
                            normalized_value=e.normalized_value,
                            confidence=e.confidence,
                        )
                        for e in region.entities
                    ],
                )
                for region in sorted(page.regions, key=lambda r: r.reading_order)
            ],
        )
        for page in sorted(document.pages, key=lambda p: p.page_number)
    ]

    flags = [
        ReviewFlagOut(
            id=f.id,
            flag_type=f.flag_type.value,
            severity=f.severity.value,
            explanation=f.explanation,
            status=f.status.value,
            page_id=f.page_id,
            region_id=f.region_id,
            entity_id=f.entity_id,
            created_at=f.created_at,
            status_changed_at=f.status_changed_at,
        )
        for f in document.review_flags
    ]

    return DocumentDetail(
        id=document.id,
        filename=document.filename,
        upload_time=document.upload_time,
        status=document.status.value,
        raw_image_path=document.raw_image_path,
        pages=pages,
        flags=flags,
    )


@router.get("/{document_id}/image")
def get_document_image(
    document_id: UUID,
    annotate: bool = Query(False, description="Draw region bounding boxes, colored by region type"),
    db: Session = Depends(get_db),
):
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")

    image_path = Path(document.raw_image_path)
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail="image file not found on disk")

    if not annotate:
        return FileResponse(image_path)

    image = cv2.imread(str(image_path))
    if image is None:
        raise HTTPException(status_code=500, detail="failed to read image file")

    regions = db.scalars(select(Region).join(Page).where(Page.document_id == document_id)).all()
    for region in regions:
        color = _REGION_TYPE_COLORS.get(region.region_type.value, _DEFAULT_REGION_COLOR)
        top_left = (region.bbox_x, region.bbox_y)
        bottom_right = (region.bbox_x + region.bbox_w, region.bbox_y + region.bbox_h)
        cv2.rectangle(image, top_left, bottom_right, color, thickness=2)

    ok, buffer = cv2.imencode(".png", image)
    if not ok:
        raise HTTPException(status_code=500, detail="failed to encode annotated image")

    return StreamingResponse(io.BytesIO(buffer.tobytes()), media_type="image/png")
