from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from review_api.auth import require_api_token
from review_api.rate_limit import enforce_rate_limit
from review_api.schemas import SearchResponse, SearchResultItem
from storage import queries
from storage.db import get_db
from storage.models import EntityType

router = APIRouter(
    prefix="/search",
    tags=["search"],
    dependencies=[Depends(enforce_rate_limit), Depends(require_api_token)],
)

DEFAULT_LIMIT = 20
MAX_LIMIT = 200


@router.get("", response_model=SearchResponse)
def search(
    q: str = Query(..., min_length=1, description="Full-text search query"),
    date_from: date | None = Query(None, description="Only pages that also mention a date on/after this"),
    date_to: date | None = Query(None, description="Only pages that also mention a date on/before this"),
    entity_type: EntityType | None = Query(None, description="Only pages with at least one entity of this type"),
    location: str | None = Query(None, description="Only pages mentioning a location fuzzy-matching this"),
    min_confidence: float | None = Query(
        None, ge=0, le=100, description="Only pages with at least one region at or above this OCR confidence"
    ),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> SearchResponse:
    """Ranked full-text search with snippet highlighting, filterable by
    date range, entity type, location, and minimum OCR confidence. See
    storage.queries.search_documents for how the filters combine.
    """
    results, total = queries.search_documents(
        db,
        q,
        date_from=date_from,
        date_to=date_to,
        entity_type=entity_type,
        location=location,
        min_confidence=min_confidence,
        limit=limit,
        offset=offset,
    )
    return SearchResponse(
        results=[
            SearchResultItem(
                page_id=r["page_id"],
                document_id=r["document_id"],
                filename=r["filename"],
                page_number=r["page_number"],
                rank=r["rank"],
                snippet=r["snippet"],
            )
            for r in results
        ],
        limit=limit,
        offset=offset,
        total=total,
    )
