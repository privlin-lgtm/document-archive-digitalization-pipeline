"""entity_corrections: audit trail for PATCH /entities/{id}

Stage 7 requires human corrections to record original + corrected value +
reviewer + timestamp without destroying the original OCR output.
entity.raw_text stays untouched forever; entity.normalized_value is updated
to the corrected value (so downstream search/queries see the current best
value); each correction is appended here rather than overwriting a single
column, preserving full history.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "entity_corrections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "entity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("original_value", sa.Text(), nullable=True),
        sa.Column("corrected_value", sa.Text(), nullable=False),
        sa.Column("reviewer", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_entity_corrections_entity_id", "entity_corrections", ["entity_id"])


def downgrade() -> None:
    op.drop_table("entity_corrections")
