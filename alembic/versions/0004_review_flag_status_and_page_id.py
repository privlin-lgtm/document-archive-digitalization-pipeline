"""review_flags: status (open/resolved/dismissed) + page_id

Stage 6 (extraction/anomalies.py) requires review_flags to be tied to
document/page/entity with a status field (open/resolved/dismissed) for the
review workflow — the original migration only had a boolean `resolved` and
no `page_id`. Replaces `resolved` with `status` (existing True/False data is
preserved: resolved -> 'resolved', not resolved -> 'open') and renames
`resolved_at` to `status_changed_at` (now meaningful for a dismiss too, not
just a resolve). Adds `page_id` so a flag can be tied to a page without
requiring a specific region.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "review_flags",
        sa.Column("page_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pages.id", ondelete="CASCADE"), nullable=True),
    )

    review_flag_status = sa.Enum("open", "resolved", "dismissed", name="review_flag_status")
    review_flag_status.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "review_flags",
        sa.Column("status", review_flag_status, nullable=False, server_default="open"),
    )
    op.execute("UPDATE review_flags SET status = 'resolved' WHERE resolved IS TRUE")

    op.drop_index("ix_review_flags_unresolved", table_name="review_flags")
    op.drop_column("review_flags", "resolved")
    op.alter_column("review_flags", "resolved_at", new_column_name="status_changed_at")

    op.create_index("ix_review_flags_open", "review_flags", ["status"], postgresql_where=sa.text("status = 'open'"))


def downgrade() -> None:
    op.drop_index("ix_review_flags_open", table_name="review_flags")
    op.alter_column("review_flags", "status_changed_at", new_column_name="resolved_at")
    op.add_column("review_flags", sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute("UPDATE review_flags SET resolved = TRUE WHERE status = 'resolved'")
    op.create_index(
        "ix_review_flags_unresolved", "review_flags", ["resolved"], postgresql_where=sa.text("NOT resolved")
    )
    op.drop_column("review_flags", "status")
    op.execute("DROP TYPE IF EXISTS review_flag_status")
    op.drop_column("review_flags", "page_id")
