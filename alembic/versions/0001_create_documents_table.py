"""create documents table

Revision ID: 0001
Revises:
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

document_status = postgresql.ENUM(
    "uploaded",
    "preprocessing",
    "ocr_running",
    "ocr_done",
    "extracting",
    "indexed",
    "needs_review",
    "error",
    name="document_status",
)


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("upload_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", document_status, nullable=False),
        sa.Column("raw_image_path", sa.String(length=1024), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("documents")
    document_status.drop(op.get_bind(), checkfirst=True)
