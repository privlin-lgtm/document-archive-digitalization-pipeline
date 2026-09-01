from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from storage.db import get_db
from storage.models import Document, DocumentStatus

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("")
def list_documents(
    status: DocumentStatus | None = None, db: Session = Depends(get_db)
) -> list[dict]:
    stmt = select(Document)
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
