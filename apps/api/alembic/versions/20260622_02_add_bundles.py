"""Add bundles index table."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260622_02"
down_revision = "20260622_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bundles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("object_name", sa.String(length=1024), nullable=False),
        sa.Column("publisher_slug", sa.String(length=255), nullable=False),
        sa.Column("book_name", sa.String(length=512), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("file_name", sa.String(length=512), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("app_version", sa.String(length=50), nullable=True),
        sa.Column("book_id", sa.Integer(), nullable=True),
        sa.Column("group_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_name", name="uq_bundles_object_name"),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_bundles_publisher_slug", "bundles", ["publisher_slug"])
    op.create_index("ix_bundles_book_name", "bundles", ["book_name"])
    op.create_index("ix_bundles_book_id", "bundles", ["book_id"])
    op.create_index("ix_bundles_group_id", "bundles", ["group_id"])


def downgrade() -> None:
    op.drop_index("ix_bundles_group_id", table_name="bundles")
    op.drop_index("ix_bundles_book_id", table_name="bundles")
    op.drop_index("ix_bundles_book_name", table_name="bundles")
    op.drop_index("ix_bundles_publisher_slug", table_name="bundles")
    op.drop_table("bundles")
