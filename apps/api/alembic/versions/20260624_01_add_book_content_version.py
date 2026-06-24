"""Add content_version to books table.

A monotonic per-book counter that is bumped every time a book's CONTENT
(config.json / activities / pages) is (re)written to object storage. Lets
downstream consumers (e.g. the LMS sync) cheaply detect "did this book's
content change since I last synced?" straight from the book LIST API, without
fetching config.json or issuing a per-book storage metadata call.

Backfill: existing rows default to 1 (a stable non-null baseline). On their
first sync after this migration consumers will treat the book as
"seen at version 1" and resync once; thereafter only real content writes bump
the value. An integer counter is preferred over a wall-clock timestamp so the
signal is immune to clock skew and trivially comparable.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260624_01"
down_revision = "20260622_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "books",
        sa.Column(
            "content_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )


def downgrade() -> None:
    op.drop_column("books", "content_version")
