"""Tests for _first_matching: locating the root config.json in an upload zip.

Regression: a macOS zip adds ``__MACOSX/.../._config.json`` (binary AppleDouble)
for files with extended attributes; the old endswith match could pick it,
yielding a bogus "config.json is not valid JSON" error.
"""

from __future__ import annotations

import pytest

from app.routers.books import _first_matching


@pytest.mark.parametrize(
    "names,expected",
    [
        # Real config.json first, AppleDouble second.
        (
            ["Book/config.json", "__MACOSX/Book/._config.json"],
            "Book/config.json",
        ),
        # AppleDouble FIRST — the old code picked this binary entry and failed.
        (
            ["__MACOSX/Book/._config.json", "Book/config.json"],
            "Book/config.json",
        ),
        # Prefer the shallowest (root) config.json over a nested one.
        (
            ["Book/modules/config.json", "Book/config.json"],
            "Book/config.json",
        ),
        # config.json at the very archive root.
        (["config.json", "__MACOSX/._config.json"], "config.json"),
        # Exact basename — must not match old_config.json / myconfig.json.
        (["Book/old_config.json", "Book/config.json"], "Book/config.json"),
        (["Book/old_config.json"], None),
        # Only macOS junk → nothing.
        (["__MACOSX/Book/._config.json"], None),
    ],
)
def test_first_matching_config(names: list[str], expected: str | None) -> None:
    assert _first_matching(names, "config.json") == expected


def test_first_matching_only_matches_basename_filename() -> None:
    # metadata.json lookup ignores config.json entries.
    names = ["Book/config.json", "Book/metadata.json"]
    assert _first_matching(names, "metadata.json") == "Book/metadata.json"
