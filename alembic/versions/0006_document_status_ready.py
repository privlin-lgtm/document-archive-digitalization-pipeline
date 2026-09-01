"""add 'ready' document status value

Stage 9 wires the pipeline all the way through to a final status: `ready`
once anomaly flagging finds nothing to review, `needs_review` when it does.
The enum had `needs_review` already but no matching "clean" terminal value.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE document_status ADD VALUE IF NOT EXISTS 'ready'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE; see 0002's downgrade for
    # the same limitation -- removing a value safely requires rebuilding the
    # type, not safe to do automatically if any row already uses it.
    pass
