"""add ocr_partial status value and index on documents.status

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # The review workflow's core query (WHERE status = 'needs_review') was
    # doing a full table scan with no index at archive scale.
    op.create_index("ix_documents_status", "documents", ["status"])

    # ocr.engine.OCRResult.status models "ok"/"ocr_partial"/"failed", but the
    # documents.status enum had no matching value for a partial OCR result.
    op.execute("ALTER TYPE document_status ADD VALUE IF NOT EXISTS 'ocr_partial'")


def downgrade() -> None:
    op.drop_index("ix_documents_status", table_name="documents")
    # Postgres has no ALTER TYPE ... DROP VALUE; removing an enum value
    # requires rebuilding the type, which isn't safe to do automatically
    # here if any row already uses it. Left as a manual step if ever needed.
