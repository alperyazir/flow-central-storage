"""Add book_groups table + books.group_id, wire bundles.group_id FK.

Drops any pre-existing ``book_groups`` table first: an earlier, reverted
book-groups branch (revision 20260618_01, since removed) left an orphan table
with a different schema (``primary_book_id``) in some dev DBs. ``IF EXISTS``
makes this a no-op on clean databases.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260622_03"
down_revision = "20260622_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Remove the orphan table from the reverted design (no FK references it:
    # books.group_id never existed in that design).
    op.execute("DROP TABLE IF EXISTS book_groups CASCADE")

    op.create_table(
        "book_groups",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("publisher_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["publisher_id"], ["publishers.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_book_groups_publisher_id", "book_groups", ["publisher_id"])

    # books.group_id -> book_groups.id (SET NULL: deleting a group ungroups books)
    op.add_column("books", sa.Column("group_id", sa.Integer(), nullable=True))
    op.create_index("ix_books_group_id", "books", ["group_id"])
    op.create_foreign_key(
        "fk_books_group_id", "books", "book_groups", ["group_id"], ["id"], ondelete="SET NULL"
    )

    # bundles.group_id column was added in 20260622_02 without a FK; wire it now.
    op.create_foreign_key(
        "fk_bundles_group_id", "bundles", "book_groups", ["group_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint("fk_bundles_group_id", "bundles", type_="foreignkey")
    op.drop_constraint("fk_books_group_id", "books", type_="foreignkey")
    op.drop_index("ix_books_group_id", table_name="books")
    op.drop_column("books", "group_id")
    op.drop_index("ix_book_groups_publisher_id", table_name="book_groups")
    op.drop_table("book_groups")
