"""Add slug column to publishers table."""

from __future__ import annotations

import re

import sqlalchemy as sa

from alembic import op

revision = "20260413_01"
down_revision = "20260108_01"
branch_labels = None
depends_on = None


def _slugify(text: str) -> str:
    """Create a URL-safe slug from text."""
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


def upgrade() -> None:
    # Add slug column as nullable first
    op.add_column("publishers", sa.Column("slug", sa.String(255), nullable=True))

    # Populate slugs from existing names
    conn = op.get_bind()
    publishers = conn.execute(sa.text("SELECT id, name FROM publishers")).fetchall()
    used_slugs: set[str] = set()
    for pub_id, name in publishers:
        slug = _slugify(name)
        # Handle duplicates
        base_slug = slug
        counter = 2
        while slug in used_slugs:
            slug = f"{base_slug}-{counter}"
            counter += 1
        used_slugs.add(slug)
        conn.execute(sa.text("UPDATE publishers SET slug = :slug WHERE id = :id"), {"slug": slug, "id": pub_id})

    # Make slug non-nullable and add unique constraint
    op.alter_column("publishers", "slug", nullable=False)
    op.create_unique_constraint("uq_publishers_slug", "publishers", ["slug"])


def downgrade() -> None:
    op.drop_constraint("uq_publishers_slug", "publishers", type_="unique")
    op.drop_column("publishers", "slug")
