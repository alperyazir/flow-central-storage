"""Add parent_publisher_id (umbrella hierarchy) to publishers table."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260606_01"
down_revision = "20260422_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable self-FK; existing rows stay NULL (= top-level). No backfill.
    op.add_column(
        "publishers",
        sa.Column("parent_publisher_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_publishers_parent_publisher_id",
        "publishers",
        ["parent_publisher_id"],
    )
    op.create_foreign_key(
        "fk_publishers_parent_publisher_id",
        "publishers",
        "publishers",
        ["parent_publisher_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_publishers_parent_publisher_id", "publishers", type_="foreignkey")
    op.drop_index("ix_publishers_parent_publisher_id", table_name="publishers")
    op.drop_column("publishers", "parent_publisher_id")
