from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from review_api.auth import require_api_token
from review_api.schemas import EntityCorrectionOut, EntityCorrectionRequest
from storage.db import get_db
from storage.models import Entity, EntityCorrection

router = APIRouter(prefix="/entities", tags=["entities"], dependencies=[Depends(require_api_token)])


@router.patch("/{entity_id}", response_model=EntityCorrectionOut)
def correct_entity(entity_id: UUID, body: EntityCorrectionRequest, db: Session = Depends(get_db)) -> EntityCorrectionOut:
    """Record a human correction of an extracted entity's value.

    `entity.raw_text` (the original OCR output) is never modified.
    `entity.normalized_value` is updated to the corrected value so
    downstream search/queries see the current best value, and the
    correction is appended to `entity_corrections` — a full audit trail,
    not an overwrite — recording what the value was before this change.
    """
    entity = db.get(Entity, entity_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="entity not found")

    original_value = entity.normalized_value
    correction = EntityCorrection(
        entity_id=entity.id,
        original_value=original_value,
        corrected_value=body.corrected_value,
        reviewer=body.reviewer,
    )
    entity.normalized_value = body.corrected_value

    db.add(correction)
    db.commit()
    db.refresh(correction)

    return EntityCorrectionOut(
        entity_id=entity.id,
        original_value=original_value,
        corrected_value=correction.corrected_value,
        reviewer=correction.reviewer,
        corrected_at=correction.created_at,
    )
