"""What an upload must leave behind.

A scan of R2 on 2026-09-22 found ~7.2 GB of files across 232 books that no
reader ever sees: 5.71 GB of extra PDFs under raw/ (28 byte-identical copies of
the original, 11 covers), 1.53 GB of crop leftovers under images/ that no JSON
points at, and a handful of editor backups the old filter's suffix checks
missed. Every re-upload put them back, so the rules belong here rather than in
a cleanup script.
"""

from __future__ import annotations

import io
import json
import zipfile

from app.services.storage import iter_zip_entries


def _archive(files: dict[str, str | bytes]) -> zipfile.ZipFile:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    buffer.seek(0)
    return zipfile.ZipFile(buffer)


def _paths(files: dict[str, str | bytes], strip_root: str | None = None) -> list[str]:
    with _archive(files) as archive:
        return [final for _entry, final in iter_zip_entries(archive, strip_root=strip_root)]


CONFIG = json.dumps(
    {
        "book": "Demo",
        "pages": [
            {"image": "./books/Demo/images/p01.png"},
            {"background": "images/p02.jpg"},
            {"note": "see p03 for details"},
        ],
    }
)


class TestRawPdfWhitelist:
    def test_only_the_original_and_answered_pdfs_survive(self) -> None:
        paths = _paths(
            {
                "config.json": CONFIG,
                "raw/original.pdf": "pdf",
                "raw/answered.pdf": "pdf",
                "raw/KAPAK.pdf": "cover",
                "raw/Demo_Book_copy.pdf": "duplicate",
            }
        )

        assert sorted(paths) == ["config.json", "raw/answered.pdf", "raw/original.pdf"]

    def test_the_names_are_matched_without_case(self) -> None:
        paths = _paths({"raw/Original.PDF": "pdf", "raw/ANSWERED.pdf": "pdf"})

        assert len(paths) == 2

    def test_other_file_types_under_raw_are_untouched(self) -> None:
        """Only PDFs are listed by the player; the rest of raw/ is left alone."""
        paths = _paths({"raw/original.pdf": "pdf", "raw/source.indd": "x", "raw/notes.txt": "x"})

        assert sorted(paths) == ["raw/notes.txt", "raw/original.pdf", "raw/source.indd"]

    def test_a_pdf_deeper_in_the_tree_is_not_a_raw_pdf(self) -> None:
        paths = _paths({"raw/extra/cover.pdf": "x", "documents/worksheet.pdf": "x"})

        assert sorted(paths) == ["documents/worksheet.pdf", "raw/extra/cover.pdf"]

    def test_the_rule_applies_after_the_root_folder_is_stripped(self) -> None:
        paths = _paths(
            {"Demo/raw/original.pdf": "pdf", "Demo/raw/kapak.pdf": "cover"}, strip_root="Demo"
        )

        assert paths == ["raw/original.pdf"]


class TestUnreferencedImages:
    def test_an_image_no_json_mentions_is_left_out(self) -> None:
        paths = _paths(
            {
                "config.json": CONFIG,
                "images/p01.png": "used by full path",
                "images/p02.jpg": "used by file name",
                "images/p03.png": "used by stem as a word",
                "images/p01_crop_1732020304.png": "editor leftover",
                "images/p04s2.png": "old page",
            }
        )

        assert sorted(paths) == ["config.json", "images/p01.png", "images/p02.jpg", "images/p03.png"]

    def test_games_json_counts_as_a_reference_too(self) -> None:
        paths = _paths(
            {
                "config.json": json.dumps({"pages": []}),
                "games.json": json.dumps({"cards": [{"front": "images/card_a.png"}]}),
                "images/card_a.png": "used",
                "images/card_b.png": "orphan",
            }
        )

        assert "images/card_a.png" in paths
        assert "images/card_b.png" not in paths

    def test_audio_video_and_raw_are_never_dropped_this_way(self) -> None:
        """The player finds these by directory scan, so absence from the JSON means nothing."""
        paths = _paths(
            {
                "config.json": json.dumps({"pages": []}),
                "audio/track.mp3": "x",
                "audio/audio.json": json.dumps({"files": []}),
                "videos/intro.mp4": "x",
                "videos/intro.srt": "x",
                "raw/original.pdf": "x",
                "images/orphan.png": "x",
            }
        )

        assert "audio/track.mp3" in paths
        assert "videos/intro.mp4" in paths
        assert "videos/intro.srt" in paths
        assert "raw/original.pdf" in paths
        assert "images/orphan.png" not in paths

    def test_an_unreadable_json_keeps_every_image(self) -> None:
        """Without knowing the references, dropping an image is a guess."""
        paths = _paths(
            {
                "config.json": "{ this is not json",
                "images/p01.png": "x",
                "images/mystery.png": "x",
            }
        )

        assert "images/p01.png" in paths
        assert "images/mystery.png" in paths

    def test_a_book_without_json_keeps_every_image(self) -> None:
        paths = _paths({"images/p01.png": "x", "images/p02.png": "x"})

        assert len(paths) == 2

    def test_a_stem_only_matches_as_a_whole_word(self) -> None:
        """"p1" in the config must not keep "p10.png" alive."""
        paths = _paths(
            {
                "config.json": json.dumps({"pages": [{"image": "p1.png"}]}),
                "images/p1.png": "x",
                "images/p10.png": "x",
            }
        )

        assert "images/p1.png" in paths
        assert "images/p10.png" not in paths


class TestJunkFiles:
    def test_the_editor_scratch_folder_is_skipped(self) -> None:
        paths = _paths(
            {
                "config.json": json.dumps({"pages": []}),
                "temp/page_001.jpg": "preview",
                "temp/nested/page_002.jpg": "preview",
            }
        )

        assert paths == ["config.json"]

    def test_temp_lower_in_the_tree_is_kept(self) -> None:
        """Only a top-level temp/ is the editor's; a temp/ inside images/ is not."""
        paths = _paths({"images/temp/p01.png": "x", "config.json": json.dumps({"pages": [{"i": "images/temp/p01.png"}]})})

        assert "images/temp/p01.png" in paths

    def test_extensionless_fbinf_and_editor_backups_are_skipped(self) -> None:
        paths = _paths(
            {
                "config.json": json.dumps({"pages": []}),
                "fbinf": "index",
                "assets/fbinf": "index",
                "config_json.bak_before_imgfix": "backup",
                "config_json.bak_before_audiofix": "backup",
                "config.json.bak.safe": "backup",
                "games.json.bak": "backup",
            }
        )

        assert paths == ["config.json"]
