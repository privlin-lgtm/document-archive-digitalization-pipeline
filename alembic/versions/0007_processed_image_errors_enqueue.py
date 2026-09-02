"""processed image paths, error_message, enqueue_failed status

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE document_status ADD VALUE IF NOT EXISTS 'enqueue_failed'")
    op.add_column("documents", sa.Column("processed_image_path", sa.String(length=1024), nullable=True))
    op.add_column("documents", sa.Column("error_message", sa.Text(), nullable=True))
    op.add_column("pages", sa.Column("processed_image_path", sa.String(length=1024), nullable=True))


def downgrade() -> None:
    op.drop_column("pages", "processed_image_path")
    op.drop_column("documents", "error_message")
    op.drop_column("documents", "processed_image_path")
