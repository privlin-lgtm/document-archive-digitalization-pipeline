from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from review_api.auth import require_api_token
from review_api.schemas import (
    ReviewFlagListResponse,
    ReviewFlagOut,
    ReviewFlagUpdateRequest,
    ReviewFlagWithDocument,
)
from storage.db import get_db
from storage.models import Document, FlagSeverity, ReviewFlag, ReviewFlagStatus

router = APIRouter(prefix="/review_flags", tags=["review_flags"], dependencies=[Depends(require_api_token)])

DEFAULT_LIMIT = 50
MAX_LIMIT = 500

_SEVERITY_ORDER = case(
    (ReviewFlag.severity == FlagSeverity.high, 0),
    (ReviewFlag.severity == FlagSeverity.medium, 1),
    (ReviewFlag.severity == FlagSeverity.low, 2),
    else_=3,
)


@router.get("", response_model=ReviewFlagListResponse)
def list_review_flags(
    status: ReviewFlagStatus | None = Query(
        ReviewFlagStatus.open, description="Defaults to open flags only; pass null/omit filter for all statuses"
    ),
    severity: FlagSeverity | None = None,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> ReviewFlagListResponse:
    """The review queue: flags sorted by severity (high first), then oldest
    first, each with enough document context (filename) to link straight
    into the document view.
    """
    stmt = select(ReviewFlag, Document.filename).join(Document, Document.id == ReviewFlag.document_id)
    if status is not None:
        stmt = stmt.where(ReviewFlag.status == status)
    if severity is not None:
        stmt = stmt.where(ReviewFlag.severity == severity)

    total = db.scalar(select(func.count()).select_from(stmt.with_only_columns(ReviewFlag.id).subquery())) or 0

    stmt = stmt.order_by(_SEVERITY_ORDER, ReviewFlag.created_at.asc()).limit(limit).offset(offset)
    rows = db.execute(stmt).all()

    return ReviewFlagListResponse(
        results=[
            ReviewFlagWithDocument(
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
                document_id=flag.document_id,
                document_filename=filename,
            )
            for flag, filename in rows
        ],
        limit=limit,
        offset=offset,
        total=total,
    )


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
