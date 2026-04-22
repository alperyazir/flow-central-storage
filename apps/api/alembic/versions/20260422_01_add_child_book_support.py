"""Add child book support: parent_book_id FK and book_type column."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260422_01"
down_revision = "20260413_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "books",
        sa.Column(
            "parent_book_id",
            sa.Integer(),
            sa.ForeignKey("books.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column(
        "books",
        sa.Column(
            "book_type",
            sa.String(20),
            nullable=False,
            server_default="standard",
        ),
    )
    op.create_index("ix_books_parent_book_id", "books", ["parent_book_id"])


def downgrade() -> None:
    op.drop_index("ix_books_parent_book_id", table_name="books")
    op.drop_column("books", "book_type")
    op.drop_column("books", "parent_book_id")
