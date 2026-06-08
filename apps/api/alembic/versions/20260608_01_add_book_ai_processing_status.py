"""Add ai_processing_status and ai_processed_at to books table."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260608_01"
down_revision = "20260606_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Persistent mirror of the AI processing state so every view can show a
    # reliable badge independent of the TTL-bound Redis job. Existing rows stay
    # NULL (= never processed); no backfill.
    op.add_column(
        "books",
        sa.Column("ai_processing_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "books",
        sa.Column("ai_processed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("books", "ai_processed_at")
    op.drop_column("books", "ai_processing_status")
