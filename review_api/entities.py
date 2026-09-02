from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from review_api.auth import require_api_token
from review_api.principal import AuthPrincipal
from review_api.rate_limit import enforce_rate_limit
from review_api.schemas import EntityCorrectionOut, EntityCorrectionRequest
from storage.db import get_db
from storage.models import Entity, EntityCorrection, EntityType
from storage.typed_values import parse_amount_value, parse_date_value

router = APIRouter(
    prefix="/entities",
    tags=["entities"],
    dependencies=[Depends(enforce_rate_limit), Depends(require_api_token)],
)


@router.patch("/{entity_id}", response_model=EntityCorrectionOut)
def correct_entity(
    entity_id: UUID,
    body: EntityCorrectionRequest,
    db: Session = Depends(get_db),
    principal: AuthPrincipal = Depends(require_api_token),
) -> EntityCorrectionOut:
    entity = db.get(Entity, entity_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="entity not found")

    original_value = entity.normalized_value
    correction = EntityCorrection(
        entity_id=entity.id,
        original_value=original_value,
        corrected_value=body.corrected_value,
        reviewer=principal.reviewer,
    )
    entity.normalized_value = body.corrected_value
    if entity.entity_type == EntityType.date:
        entity.date_value = parse_date_value(body.corrected_value)
    elif entity.entity_type == EntityType.amount:
        amount, currency = parse_amount_value(body.corrected_value)
        entity.amount_value = amount
        entity.amount_currency = currency

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
