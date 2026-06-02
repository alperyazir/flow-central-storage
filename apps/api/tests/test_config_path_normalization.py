"""Regression tests for config.json / games.json path normalization.

Covers the bug where a book whose storage folder was normalized one way
(e.g. the publisher's ``oe`` spelling) had its config paths normalized
differently (``ö`` -> ``o``), leaving config references pointing at a folder
that does not exist in storage.
"""

from __future__ import annotations

import json

from app.services.storage import _update_config_paths


def _run(config: dict, *, book_name: str, original: str | None) -> dict:
    out = _update_config_paths(
        json.dumps(config, ensure_ascii=False).encode("utf-8"),
        {},
        book_name=book_name,
        original_book_folder=original,
    )
    return json.loads(out.decode("utf-8"))


def test_book_folder_replaced_without_books_marker():
    """A path with no ``books/`` marker still gets the canonical book folder."""
    book_name = "Der_Hase_und_die_Schildkroete"  # storage folder
    original = "Der_Hase_und_die_Schildkröte"  # original (real ö) in config

    result = _run(
        {"bg": f"./{original}/images/1.PNG"},
        book_name=book_name,
        original=original,
    )

    assert result["bg"] == f"./{book_name}/images/1.png"


def test_book_folder_replaced_after_books_marker():
    book_name = "Der_Hase_und_die_Schildkroete"
    original = "Der_Hase_und_die_Schildkröte"

    result = _run(
        {"bg": f"./books/{original}/audio/Ünite 1.mp3"},
        book_name=book_name,
        original=original,
    )

    assert result["bg"] == f"./books/{book_name}/audio/Unite_1.mp3"


def test_books_marker_is_case_insensitive():
    book_name = "MyBook"
    result = _run(
        {"bg": "./Books/Original Folder/x.png"},
        book_name=book_name,
        original="Original Folder",
    )
    assert result["bg"] == f"./Books/{book_name}/x.png"


def test_non_path_values_untouched():
    result = _run(
        {"title": "Der Hase und die Schildkröte", "lang": "de"},
        book_name="X",
        original="Der_Hase_und_die_Schildkröte",
    )
    assert result["title"] == "Der Hase und die Schildkröte"
    assert result["lang"] == "de"


def test_falls_back_to_part_normalization_without_original():
    """With no original folder and no books marker, segments are still cleaned."""
    result = _run(
        {"bg": "./some folder/Ünite.PNG"},
        book_name="X",
        original=None,
    )
    assert result["bg"] == "./some_folder/Unite.png"
