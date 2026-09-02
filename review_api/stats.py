from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from review_api.auth import require_api_token
from review_api.rate_limit import enforce_rate_limit
from review_api.schemas import StatsResponse
from storage.db import get_db
from storage.models import (
    Document,
    DocumentStatus,
    Entity,
    OCRResultRecord,
    ReviewFlag,
    ReviewFlagStatus,
)

router = APIRouter(
    prefix="/stats",
    tags=["stats"],
    dependencies=[Depends(enforce_rate_limit), Depends(require_api_token)],
)


@router.get("", response_model=StatsResponse)
def get_stats(db: Session = Depends(get_db)) -> StatsResponse:
    """Dashboard summary: documents processed, pending review count,
    average confidence, flags by type.
    """
    total_documents = db.scalar(select(func.count(Document.id))) or 0
    documents_indexed = (
        db.scalar(select(func.count(Document.id)).where(Document.status == DocumentStatus.indexed)) or 0
    )
    documents_needing_review = (
        db.scalar(select(func.count(Document.id)).where(Document.status == DocumentStatus.needs_review)) or 0
    )
    open_review_flags = (
        db.scalar(select(func.count(ReviewFlag.id)).where(ReviewFlag.status == ReviewFlagStatus.open)) or 0
    )
    average_ocr_confidence = db.scalar(select(func.avg(OCRResultRecord.confidence)))
    average_entity_confidence = db.scalar(select(func.avg(Entity.confidence)))

    flag_type_rows = db.execute(
        select(ReviewFlag.flag_type, func.count(ReviewFlag.id))
        .where(ReviewFlag.status == ReviewFlagStatus.open)
        .group_by(ReviewFlag.flag_type)
    ).all()

    return StatsResponse(
        total_documents=total_documents,
        documents_indexed=documents_indexed,
        documents_needing_review=documents_needing_review,
        open_review_flags=open_review_flags,
        average_ocr_confidence=average_ocr_confidence,
        average_entity_confidence=average_entity_confidence,
        open_flags_by_type={flag_type.value: count for flag_type, count in flag_type_rows},
    )
