"""pages, regions, ocr_results, entities, review_flags + search indexing

Adds the rest of the archive schema on top of `documents`: pages (with a
generated tsvector column + GIN index for full-text search), regions
(layout boxes from ocr.layout), ocr_results (one per region), entities
(typed: person/date/location/amount, with a BRIN index on date_value and
trigram indexes for fuzzy name search), and review_flags.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # pg_trgm powers the trigram GIN indexes below (fuzzy "did you mean"
    # search against OCR-garbled entity names).
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "pages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("full_text", sa.Text(), nullable=True),
        # STORED generated column: kept in sync by Postgres on every write,
        # so application code never has to remember to update it. The
        # 'english' config is a literal here (required for the expression
        # to be IMMUTABLE, which GENERATED ALWAYS AS requires).
        sa.Column(
            "full_text_search",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', coalesce(full_text, ''))", persisted=True),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("document_id", "page_number", name="uq_pages_document_page_number"),
    )
    op.create_index("ix_pages_document_id", "pages", ["document_id"])
    # GIN is the standard index type for tsvector: supports the @@ match
    # operator efficiently over a column that holds a *set* of lexemes per
    # row (unlike BRIN/B-tree, which assume a single scalar/orderable value).
    op.create_index(
        "ix_pages_full_text_search_gin", "pages", ["full_text_search"], postgresql_using="gin"
    )

    op.create_table(
        "regions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "page_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pages.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("bbox_x", sa.Integer(), nullable=False),
        sa.Column("bbox_y", sa.Integer(), nullable=False),
        sa.Column("bbox_w", sa.Integer(), nullable=False),
        sa.Column("bbox_h", sa.Integer(), nullable=False),
        sa.Column(
            "region_type",
            sa.Enum(
                "paragraph", "table", "signature", "margin_annotation", "stamp", name="region_type"
            ),
            nullable=False,
        ),
        sa.Column("reading_order", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_regions_page_id", "regions", ["page_id"])

    op.create_table(
        "ocr_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "region_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("regions.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("engine", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "status", sa.Enum("ok", "ocr_partial", "failed", name="ocr_result_status"), nullable=False
        ),
        sa.Column("notes", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "entities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "region_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("regions.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "entity_type", sa.Enum("person", "date", "location", "amount", name="entity_type"), nullable=False
        ),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("normalized_value", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("start_char", sa.Integer(), nullable=False),
        sa.Column("end_char", sa.Integer(), nullable=False),
        sa.Column("date_value", sa.Date(), nullable=True),
        sa.Column("amount_value", sa.Numeric(14, 2), nullable=True),
        sa.Column("amount_currency", sa.String(length=8), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_entities_region_id", "entities", ["region_id"])
    op.create_index("ix_entities_entity_type", "entities", ["entity_type"])
    # entities.amount_value: a normal, general-purpose scalar range column —
    # plain B-tree is the right choice (contrast with date_value below).
    op.create_index("ix_entities_amount_value", "entities", ["amount_value"])
    # entities.date_value: BRIN instead of B-tree. BRIN stores per-block
    # min/max summaries rather than an entry per row, so it's a small
    # fraction of a B-tree's size on a large table — but that only pays off
    # when the indexed value correlates with physical row order (BRIN
    # narrows to a range of *blocks*, then scans them; if matching rows are
    # scattered across the whole table, it degrades toward a full scan).
    # For an archive digitization pipeline, that correlation is a real
    # assumption, not automatic: it holds when documents are processed in
    # roughly the chronological order of the events/dates they describe
    # (e.g. working through a bound ledger page-by-page, or digitizing
    # yearly volumes in sequence) — a common real pattern for this domain,
    # but not guaranteed for an archive ingested in arbitrary/scanned-batch
    # order. It's also not a strict win even when it holds: measured against
    # a real seeded dataset, BRIN loses badly to B-tree on narrow/selective
    # ranges (lossy per-block rechecking) and wins on wide/low-selectivity
    # ranges (far fewer buffers touched). See queries.py's EXPLAIN ANALYZE
    # walkthrough for the real numbers and the reasoning for picking BRIN
    # here anyway (footprint + write cost + likely query shape).
    op.create_index("ix_entities_date_value_brin", "entities", ["date_value"], postgresql_using="brin")
    # Trigram GIN indexes: support fuzzy "did you mean" search (rapidfuzz at
    # the application layer already tolerates single-character OCR errors at
    # extraction time; these indexes let the *database* do the same for ad
    # hoc search against already-stored entities, via `%` / similarity()).
    op.create_index(
        "ix_entities_raw_text_trgm",
        "entities",
        ["raw_text"],
        postgresql_using="gin",
        postgresql_ops={"raw_text": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_entities_normalized_value_trgm",
        "entities",
        ["normalized_value"],
        postgresql_using="gin",
        postgresql_ops={"normalized_value": "gin_trgm_ops"},
    )

    op.create_table(
        "review_flags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "region_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("regions.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column(
            "entity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "flag_type",
            sa.Enum(
                "low_ocr_confidence", "illegible", "entity_conflict", "extraction_failure", name="review_flag_type"
            ),
            nullable=False,
        ),
        sa.Column("severity", sa.Enum("low", "medium", "high", name="review_flag_severity"), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_review_flags_document_id", "review_flags", ["document_id"])
    op.create_index(
        "ix_review_flags_unresolved", "review_flags", ["resolved"], postgresql_where=sa.text("NOT resolved")
    )


def downgrade() -> None:
    op.drop_table("review_flags")
    op.drop_table("entities")
    op.drop_table("ocr_results")
    op.drop_table("regions")
    op.drop_table("pages")
    for enum_name in (
        "review_flag_severity",
        "review_flag_type",
        "entity_type",
        "ocr_result_status",
        "region_type",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
