from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from review_api.auth import require_api_token
from review_api.schemas import ReviewFlagOut, ReviewFlagUpdateRequest
from storage.db import get_db
from storage.models import ReviewFlag, ReviewFlagStatus

router = APIRouter(prefix="/review_flags", tags=["review_flags"], dependencies=[Depends(require_api_token)])


@router.patch("/{flag_id}", response_model=ReviewFlagOut)
def update_review_flag(flag_id: UUID, body: ReviewFlagUpdateRequest, db: Session = Depends(get_db)) -> ReviewFlagOut:
    """Resolve or dismiss a review flag."""
    flag = db.get(ReviewFlag, flag_id)
    if flag is None:
        raise HTTPException(status_code=404, detail="review flag not found")

    flag.status = ReviewFlagStatus(body.status)
    flag.status_changed_at = datetime.now(UTC)
    db.commit()
    db.refresh(flag)

    return ReviewFlagOut(
        id=flag.id,
        flag_type=flag.flag_type.value,
        severity=flag.severity.value,
        explanation=flag.explanation,
        status=flag.status.value,
        page_id=flag.page_id,
        region_id=flag.region_id,
        entity_id=flag.entity_id,
        created_at=flag.created_at,
        status_changed_at=flag.status_changed_at,
    )
