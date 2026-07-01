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


def test_stem_dots_replaced_with_underscore():
    """Dots inside the filename stem fold to ``_`` (turuncu.renk.png)."""
    result = _run(
        {"bg": "./books/Book/images/turuncu.renk.png"},
        book_name="Book",
        original="Book",
        known=["images/turuncu_renk.png"],
    )
    assert result["bg"] == "./books/Book/images/turuncu_renk.png"


def test_bare_filename_inside_activity_normalized():
    """A directory-less audio ref inside an activity is reconciled to storage.

    Covers .MP3 -> .mp3, spaces -> _, and matching the uploaded basename.
    """
    result = _run(
        {"pages": [{"activity": {"type": "audio", "src": "Ünite 1.MP3"}}]},
        book_name="Book",
        original="Book",
        known=["audio/Unite_1.mp3"],
    )
    assert result["pages"][0]["activity"]["src"] == "Unite_1.mp3"


def test_bare_filename_with_stem_dots_normalized():
    """Bare ref with dotted stem + uppercase ext is folded to the stored name."""
    result = _run(
        {"a": {"sound": "turuncu.renk.MP3"}},
        book_name="Book",
        original="Book",
        known=["audio/turuncu_renk.mp3"],
    )
    assert result["a"]["sound"] == "turuncu_renk.mp3"


def test_bare_non_asset_string_untouched():
    """Plain strings that aren't media references are left alone."""
    result = _run(
        {"label": "Click me", "code": "v2.0"},
        book_name="Book",
        original="Book",
        known=["audio/song.mp3"],
    )
    assert result["label"] == "Click me"
    assert result["code"] == "v2.0"


def test_bare_filename_without_known_match_untouched():
    """A bare media name with no matching uploaded file is not mangled."""
    result = _run(
        {"x": "mystery.mp3"},
        book_name="Book",
        original="Book",
        known=["audio/other.mp3"],
    )
    assert result["x"] == "mystery.mp3"


def test_audio_json_filename_keys_normalized():
    """audio.json keys ARE the asset filenames, so a renamed file updates them.

    Reproduces the live MyEnglishPathSb bug: ``Pg 6.MP3`` was stored as
    ``Pg_6.mp3`` and config.json got fixed, but the audio.json key stayed
    ``Pg 6.MP3`` because only string *values* were normalized.
    """
    result = _run(
        {
            "Pg 6.MP3": {"duration": 1.0},
            "Pg-15.mp3": {"duration": 2.0},
        },
        book_name="Book",
        original="Book",
        known=["audio/Pg_6.mp3", "audio/Pg-15.mp3"],
    )
    assert "Pg 6.MP3" not in result
    assert result["Pg_6.mp3"] == {"duration": 1.0}
    assert result["Pg-15.mp3"] == {"duration": 2.0}  # already canonical, untouched


def test_structural_dict_keys_not_touched():
    """Non-asset keys (no media extension) pass through unchanged."""
    result = _run(
        {"duration": 1, "words": [], "lang": "en"},
        book_name="Book",
        original="Book",
        known=["audio/x.mp3"],
    )
    assert set(result.keys()) == {"duration", "words", "lang"}


def test_iter_zip_entries_skips_safe_files():
    """``.safe`` sidecar files are filtered out before reaching R2."""
    import io
    import zipfile

    from app.services.storage import iter_zip_entries

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("book/config.json", "{}")
        zf.writestr("book/audio/song.mp3", "x")
        zf.writestr("book/audio/song.safe", "x")
        zf.writestr("book/notes.SAFE", "x")
    buf.seek(0)
    with zipfile.ZipFile(buf) as zf:
        paths = [final for _entry, final in iter_zip_entries(zf)]

    assert "book/audio/song.mp3" in paths
    assert "book/config.json" in paths
    assert not any(p.lower().endswith(".safe") for p in paths)
