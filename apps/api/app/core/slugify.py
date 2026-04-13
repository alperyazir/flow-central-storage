"""Slug generation utilities."""

from __future__ import annotations

import re


def slugify(text: str) -> str:
    """Create a URL-safe slug from text.

    Examples:
        slugify("Oxford Press") -> "oxford-press"
        slugify("Dream Yayıncılık") -> "dream-yaynclk"
        slugify("  Hello   World  ") -> "hello-world"
    """
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")
