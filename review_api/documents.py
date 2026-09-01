from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from review_api.auth import require_api_token
from storage.db import get_db
from storage.models import Document, DocumentStatus

router = APIRouter(prefix="/documents", tags=["documents"], dependencies=[Depends(require_api_token)])

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 500


@router.get("")
def list_documents(
    status: DocumentStatus | None = None,
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[dict]:
    stmt = select(Document).order_by(Document.upload_time.desc()).limit(limit).offset(offset)
    if status is not None:
        stmt = stmt.where(Document.status == status)
    documents = db.scalars(stmt).all()
    return [
        {
            "id": str(d.id),
            "filename": d.filename,
            "upload_time": d.upload_time.isoformat(),
            "status": d.status.value,
            "raw_image_path": d.raw_image_path,
        }
        for d in documents
    ]


@router.get("/{document_id}")
def get_document(document_id: UUID, db: Session = Depends(get_db)) -> dict:
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    return {
        "id": str(document.id),
        "filename": document.filename,
        "upload_time": document.upload_time.isoformat(),
        "status": document.status.value,
        "raw_image_path": document.raw_image_path,
    }
