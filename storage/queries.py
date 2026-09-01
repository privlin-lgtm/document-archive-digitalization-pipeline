"""Example queries against the stage 5 schema: full-text search with
ranking/highlighting, amount range queries scoped to a date range, fuzzy
person-near-location search, and a general "did you mean" trigram lookup.

--------------------------------------------------------------------------
EXPLAIN ANALYZE walkthrough: BRIN vs B-tree on entities.date_value
--------------------------------------------------------------------------
Measured against a real synthetic dataset seeded via
scripts/seed_synthetic_data.sql: 240,008 entities (60,002 of type 'date'),
with date_value generated to trend with insertion order (see the seed
script and the migration comment) — min 1812-06-19, max 1897-03-20.

Index size (same column, same data):
    ix_entities_date_value_brin (BRIN)    24 kB
    ix_entities_date_value_btree (B-tree) 2144 kB   (~89x larger)

Query 1 — narrow range, highly selective (~959 rows, 0.4% of the table):
    SELECT id, raw_text, date_value FROM entities
    WHERE date_value BETWEEN '1850-01-01' AND '1850-12-31';

    BRIN:    Bitmap Heap Scan, 3788 buffers, 230,857 rows rejected by the
             lossy recheck, Execution Time: 22.807 ms
    B-tree:  Index Scan, 418 buffers, Execution Time: 0.249 ms
    -> B-tree wins by ~90x. BRIN only narrows the scan to *block ranges*;
       within a matching range it still has to fetch and recheck every row,
       including ones that don't actually match — expensive when very few
       rows in a wide block range are relevant.

Query 2 — wide range, low selectivity (~39,388 rows, 16% of the table):
    SELECT id, raw_text, date_value FROM entities
    WHERE date_value BETWEEN '1830-01-01' AND '1870-12-31';

    BRIN:    Bitmap Heap Scan, 904 buffers, Execution Time: 6.818 ms
    B-tree:  Index Scan, 16,185 buffers, Execution Time: 12.129 ms
    -> BRIN wins: ~1.8x faster and ~18x fewer buffers touched. When a query
       isn't selective, a B-tree ends up walking a large fraction of its
       leaf pages anyway; BRIN's coarser per-block summaries get there with
       far less index (and page-cache) overhead.

Conclusion: BRIN is not a strict win — it's a real tradeoff, and the
"right" choice depends on the dominant access pattern, which isn't fully
known yet at this stage. BRIN is still the pick here because: (1) the
~89x smaller footprint matters a lot at true archive scale, on what will
be one of the largest tables in the schema; (2) it's far cheaper to
maintain on a heavily append-only table (new documents are ingested
continuously); and (3) "how many mentions per decade/era" style broad
range queries and aggregations are a more likely dominant pattern for a
historical archive than single-year point lookups. If production query
logs later show narrow-date lookups dominate, revisit this — a B-tree (or
a partial/composite index) would serve that pattern better, and nothing
here prevents adding one alongside BRIN if needed.

BRIN also depends on date_value correlating with insertion order (see the
migration comment) — that assumption held in this seeded dataset by
construction; it should be validated against real ingestion order once the
pipeline is processing actual archive batches.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, aliased

from storage.models import Document, Entity, EntityType, OCRResultRecord, Page, Region


def full_text_search(session: Session, query_text: str, *, limit: int = 20) -> list[dict]:
    """Ranked full-text search across all pages, with a highlighted snippet.

    Uses plainto_tsquery (safe for raw user input — no tsquery syntax to
    escape) against the generated `pages.full_text_search` tsvector column,
    which the GIN index (ix_pages_full_text_search_gin) serves directly.
    """
    tsquery = func.plainto_tsquery("english", query_text)
    rank = func.ts_rank(Page.full_text_search, tsquery).label("rank")
    snippet = func.ts_headline("english", Page.full_text, tsquery).label("snippet")

    stmt = (
        select(
            Page.id.label("page_id"),
            Page.document_id,
            Document.filename,
            Page.page_number,
            rank,
            snippet,
        )
        .join(Document, Document.id == Page.document_id)
        .where(Page.full_text_search.op("@@")(tsquery))
        .order_by(rank.desc())
        .limit(limit)
    )
    return [dict(row) for row in session.execute(stmt).mappings().all()]


def amounts_over_between_dates(
    session: Session,
    min_amount: Decimal | float,
    start_date: date,
    end_date: date,
    *,
    currency: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Cash amounts over `min_amount`, recorded on a page that also mentions
    a date within [start_date, end_date].

    Amount entities don't carry a date of their own — "between two dates"
    is interpreted as page-level co-occurrence with a date entity, which
    matches how these are actually laid out (a ledger line's amount and its
    date are on the same page, usually the same region or an adjacent one).
    """
    amount_entity = aliased(Entity)
    date_entity = aliased(Entity)
    region_a = aliased(Region)
    region_d = aliased(Region)

    stmt = (
        select(
            amount_entity.id.label("entity_id"),
            amount_entity.raw_text,
            amount_entity.amount_value,
            amount_entity.amount_currency,
            date_entity.date_value,
            Document.id.label("document_id"),
            Document.filename,
        )
        .join(region_a, region_a.id == amount_entity.region_id)
        .join(Page, Page.id == region_a.page_id)
        .join(Document, Document.id == Page.document_id)
        .join(region_d, region_d.page_id == Page.id)
        .join(date_entity, date_entity.region_id == region_d.id)
        .where(
            amount_entity.entity_type == EntityType.amount,
            amount_entity.amount_value > min_amount,
            date_entity.entity_type == EntityType.date,
            date_entity.date_value.between(start_date, end_date),
        )
        .order_by(amount_entity.amount_value.desc())
        .limit(limit)
    )
    if currency is not None:
        stmt = stmt.where(amount_entity.amount_currency == currency)

    return [dict(row) for row in session.execute(stmt).mappings().all()]


def documents_mentioning_person_near_location(
    session: Session,
    person_name: str,
    location_name: str,
    *,
    similarity_threshold: float = 0.3,
    limit: int = 50,
) -> list[dict]:
    """Documents where a person entity and a location entity co-occur on
    the same page ("near" = same page, the finest granularity we have
    provenance for). Both names are matched fuzzily via pg_trgm (the `%`
    operator, backed by ix_entities_raw_text_trgm) so OCR-garbled spellings
    on either side don't prevent a match.
    """
    person_entity = aliased(Entity)
    location_entity = aliased(Entity)
    region_p = aliased(Region)
    region_l = aliased(Region)

    person_sim = func.similarity(person_entity.raw_text, person_name).label("person_similarity")
    location_sim = func.similarity(location_entity.raw_text, location_name).label("location_similarity")

    stmt = (
        select(
            Document.id.label("document_id"),
            Document.filename,
            person_entity.raw_text.label("person_text"),
            location_entity.raw_text.label("location_text"),
            person_sim,
            location_sim,
        )
        .join(region_p, region_p.id == person_entity.region_id)
        .join(Page, Page.id == region_p.page_id)
        .join(Document, Document.id == Page.document_id)
        .join(region_l, region_l.page_id == Page.id)
        .join(location_entity, location_entity.region_id == region_l.id)
        .where(
            person_entity.entity_type == EntityType.person,
            person_entity.raw_text.op("%")(person_name),
            location_entity.entity_type == EntityType.location,
            location_entity.raw_text.op("%")(location_name),
            person_sim >= similarity_threshold,
            location_sim >= similarity_threshold,
        )
        .order_by((person_sim + location_sim).desc())
        .limit(limit)
    )
    return [dict(row) for row in session.execute(stmt).mappings().all()]


def fuzzy_entity_search(
    session: Session, query_text: str, entity_type: EntityType, *, limit: int = 10
) -> list[dict]:
    """"Did you mean" search: entities of `entity_type` whose raw OCR text
    is trigram-similar to `query_text`, ranked by similarity. Demonstrates
    ix_entities_raw_text_trgm directly — useful as a standalone lookup (e.g.
    an annotation-UI autocomplete) beyond the two join queries above.
    """
    similarity = func.similarity(Entity.raw_text, query_text).label("similarity")
    stmt = (
        select(Entity.id, Entity.raw_text, Entity.normalized_value, similarity)
        .where(Entity.entity_type == entity_type, Entity.raw_text.op("%")(query_text))
        .order_by(similarity.desc())
        .limit(limit)
    )
    return [dict(row) for row in session.execute(stmt).mappings().all()]


def search_documents(
    session: Session,
    query_text: str,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    entity_type: EntityType | None = None,
    location: str | None = None,
    min_confidence: float | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Full-text search with the review_api GET /search filters layered on
    top of `full_text_search`, each applied as an independent EXISTS
    subquery so combining several is just AND-ing more predicates:

    - date_from/date_to: page also has a 'date' entity with date_value in
      this range.
    - entity_type: page has at least one entity of this type.
    - location: page has a 'location' entity fuzzy-matching (pg_trgm `%`)
      this string.
    - min_confidence: page has at least one region whose OCR confidence
      (0-100) is at or above this.

    Returns (results, total_count) for pagination.
    """
    tsquery = func.plainto_tsquery("english", query_text)
    rank = func.ts_rank(Page.full_text_search, tsquery).label("rank")
    snippet = func.ts_headline("english", Page.full_text, tsquery).label("snippet")

    filters = [Page.full_text_search.op("@@")(tsquery)]

    if date_from is not None or date_to is not None:
        date_entity, date_region = aliased(Entity), aliased(Region)
        conditions = [
            date_region.id == date_entity.region_id,
            date_region.page_id == Page.id,
            date_entity.entity_type == EntityType.date,
        ]
        if date_from is not None:
            conditions.append(date_entity.date_value >= date_from)
        if date_to is not None:
            conditions.append(date_entity.date_value <= date_to)
        filters.append(exists(select(1).select_from(date_entity).where(*conditions)))

    if entity_type is not None:
        typed_entity, typed_region = aliased(Entity), aliased(Region)
        filters.append(
            exists(
                select(1)
                .select_from(typed_entity)
                .where(
                    typed_region.id == typed_entity.region_id,
                    typed_region.page_id == Page.id,
                    typed_entity.entity_type == entity_type,
                )
            )
        )

    if location is not None:
        loc_entity, loc_region = aliased(Entity), aliased(Region)
        filters.append(
            exists(
                select(1)
                .select_from(loc_entity)
                .where(
                    loc_region.id == loc_entity.region_id,
                    loc_region.page_id == Page.id,
                    loc_entity.entity_type == EntityType.location,
                    loc_entity.raw_text.op("%")(location),
                )
            )
        )

    if min_confidence is not None:
        conf_ocr, conf_region = aliased(OCRResultRecord), aliased(Region)
        filters.append(
            exists(
                select(1)
                .select_from(conf_ocr)
                .where(
                    conf_region.id == conf_ocr.region_id,
                    conf_region.page_id == Page.id,
                    conf_ocr.confidence >= min_confidence,
                )
            )
        )

    base_stmt = (
        select(Page.id.label("page_id"), Page.document_id, Document.filename, Page.page_number, rank, snippet)
        .join(Document, Document.id == Page.document_id)
        .where(*filters)
    )

    total = session.scalar(select(func.count()).select_from(base_stmt.with_only_columns(Page.id).subquery()))
    results_stmt = base_stmt.order_by(rank.desc()).limit(limit).offset(offset)
    results = [dict(row) for row in session.execute(results_stmt).mappings().all()]
    return results, total or 0
