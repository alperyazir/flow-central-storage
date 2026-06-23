"""Tests for should_skip_bundled_path, incl. the raw/original.pdf allowlist."""

from __future__ import annotations

import pytest

from app.services.standalone_apps import should_skip_bundled_path


@pytest.mark.parametrize(
    "path,skip",
    [
        # The source PDF is kept so the new template can embed it.
        ("raw/original.pdf", False),
        ("raw/Original.PDF", False),  # case-insensitive
        # ...but the rest of raw/ is still excluded.
        ("raw/other.pdf", True),
        ("raw/notes.txt", True),
        ("raw/sub/original.pdf", True),  # only the top-level original is kept
        # A child book's nested original stays excluded (additional-resources/).
        ("additional-resources/child/raw/original.pdf", True),
        # AI artifacts and additional-resources excluded.
        ("ai-data/x.json", True),
        ("ai-content/y.json", True),
        ("additional-resources/child/index.html", True),
        # macOS metadata + junk.
        ("__MACOSX/foo", True),
        ("._hidden", True),
        ("settings.json", True),
        ("backup.bak", True),
        # Normal flowbook content is kept.
        ("config.json", False),
        ("data/pages/1.json", False),
        ("index.html", False),
        ("audio/track.mp3", False),
        # Empty / falsy.
        ("", True),
    ],
)
def test_should_skip_bundled_path(path: str, skip: bool) -> None:
    assert should_skip_bundled_path(path) is skip


def test_keep_source_pdf_false_drops_original() -> None:
    """With keep_source_pdf=False the source PDF is excluded too (smaller bundle)."""
    assert should_skip_bundled_path("raw/original.pdf", keep_source_pdf=False) is True
    # Other content is unaffected by the flag.
    assert should_skip_bundled_path("config.json", keep_source_pdf=False) is False
    # Default still keeps it.
    assert should_skip_bundled_path("raw/original.pdf") is False
