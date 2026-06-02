"""Regression tests for config.json / games.json path normalization.

Covers the bug where a book whose storage folder was normalized one way
(e.g. the publisher's ``oe`` spelling) had its config paths normalized
differently (``ö`` -> ``o``), leaving config references pointing at a folder
that does not exist in storage.
"""

from __future__ import annotations

import json

from app.services.storage import _update_config_paths


def _run(config: dict, *, book_name: str, original: str | None, known=None) -> dict:
    out = _update_config_paths(
        json.dumps(config, ensure_ascii=False).encode("utf-8"),
        {},
        book_name=book_name,
        original_book_folder=original,
        known_paths=known,
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


def test_subfolder_resolved_against_actual_file_oe_vs_umlaut():
    """A subfolder spelled with ö in config resolves to the real oe folder.

    Reproduces the live bug: the asset folder on disk is ``Schildkroete`` (oe)
    while config.json references ``Schildkröte`` (real ö). Per-segment
    normalization would give ``Schildkrote`` (ö -> o); matching the uploaded
    file fixes it to ``Schildkroete``.
    """
    book_name = "Erzhlmirdavon_1"
    known = [
        "images/Der_Hase_und_die_Schildkroete/10.png",
        "images/Der_Hase_und_die_Schildkroete/11.png",
        "images/intro/cover.png",
    ]
    result = _run(
        {
            "bad": "./books/Erzhlmirdavon_1/images/Der_Hase_und_die_Schildkröte/10.png",
            "ok": "./books/Erzhlmirdavon_1/images/intro/cover.png",
        },
        book_name=book_name,
        original="Erzhlmirdavon_1",
        known=known,
    )
    assert result["bad"] == "./books/Erzhlmirdavon_1/images/Der_Hase_und_die_Schildkroete/10.png"
    assert result["ok"] == "./books/Erzhlmirdavon_1/images/intro/cover.png"


def test_unrelated_file_not_fuzzy_matched():
    """A config path with no close real file keeps its normalized form."""
    known = ["images/cat/1.png"]
    result = _run(
        {"p": "./books/Book/audio/song.mp3"},
        book_name="Book",
        original="Book",
        known=known,
    )
    # No same-basename candidate -> plain normalization, not a wrong match.
    assert result["p"] == "./books/Book/audio/song.mp3"


def test_falls_back_to_part_normalization_without_original():
    """With no original folder and no books marker, segments are still cleaned."""
    result = _run(
        {"bg": "./some folder/Ünite.PNG"},
        book_name="X",
        original=None,
    )
    assert result["bg"] == "./some_folder/Unite.png"
