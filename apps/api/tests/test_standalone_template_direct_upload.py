"""Templates are uploaded straight to R2, not through the API.

Cloudflare sits in front of the API and refuses any request body over 100 MB
before it reaches the origin. Measured on 2026-09-24: a 120 MB body came back
413 after 2 MB had gone out, while 90 MB passed. The linux template (90.5 MB)
uploaded; win and mac, both larger, never arrived — their preflight showed up
in the logs and the POST never did.

So the browser now PUTs to a presigned URL and calls back here afterwards.
These tests cover what that callback has to do that the PUT itself cannot.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from minio.error import S3Error

from app.services.standalone_apps import (
    APP_VERSION_META_KEY,
    TEMPLATE_CACHE_DIR,
    InvalidPlatformError,
    TemplateNotFoundError,
    TemplateTooLargeError,
    finalize_template_upload,
    presign_template_upload,
)


def _stat(size: int) -> MagicMock:
    stat = MagicMock()
    stat.size = size
    stat.last_modified = datetime(2026, 9, 25, 1, 0, tzinfo=timezone.utc)
    return stat


def _missing_key() -> S3Error:
    # minio takes the response first, then the code.
    return S3Error(None, "NoSuchKey", "not found", "apps", "rid", "hid")


class TestPresign:
    def test_signs_a_put_for_the_platform_key(self) -> None:
        client = MagicMock()
        client.presigned_put_object.return_value = "https://r2.example/apps/standalone-templates/win.zip?sig"

        url, object_name = presign_template_upload(client, "apps", "win")

        assert url.startswith("https://r2.example/")
        assert object_name == "standalone-templates/win.zip"
        assert client.presigned_put_object.call_args.kwargs["object_name"] == object_name

    def test_the_url_outlives_a_slow_uplink(self) -> None:
        """A 600 MB template over a home connection takes a while."""
        client = MagicMock()

        presign_template_upload(client, "apps", "mac")

        expires = client.presigned_put_object.call_args.kwargs["expires"]
        assert expires.total_seconds() >= 3600

    def test_an_unknown_platform_is_refused(self) -> None:
        with pytest.raises(InvalidPlatformError):
            presign_template_upload(MagicMock(), "apps", "amiga")


class TestFinalize:
    def test_records_what_was_uploaded(self) -> None:
        client = MagicMock()
        client.stat_object.return_value = _stat(632_000_000)

        meta = finalize_template_upload(client, "apps", "win", "win.zip")

        assert meta.platform == "win"
        assert meta.file_size == 632_000_000
        assert meta.object_name == "standalone-templates/win.zip"

    def test_version_is_attached_without_moving_the_bytes(self) -> None:
        """A server-side copy, so a 600 MB template is not re-uploaded."""
        client = MagicMock()
        client.stat_object.return_value = _stat(1000)

        meta = finalize_template_upload(client, "apps", "mac", "mac.zip", version=" 1.12.6 ")

        assert meta.version == "1.12.6"
        assert client.copy_object.call_args.kwargs["metadata"] == {APP_VERSION_META_KEY: "1.12.6"}

    def test_no_version_means_no_copy(self) -> None:
        client = MagicMock()
        client.stat_object.return_value = _stat(1000)

        finalize_template_upload(client, "apps", "mac", "mac.zip", version="   ")

        client.copy_object.assert_not_called()

    def test_nothing_uploaded_is_a_not_found(self) -> None:
        client = MagicMock()
        client.stat_object.side_effect = _missing_key()

        with pytest.raises(TemplateNotFoundError):
            finalize_template_upload(client, "apps", "linux", "linux.zip")

    def test_an_oversized_template_is_rejected_and_removed(self) -> None:
        """The API can no longer check the size up front, so it checks it here."""
        client = MagicMock()
        client.stat_object.return_value = _stat(3_000_000_000)

        with pytest.raises(TemplateTooLargeError):
            finalize_template_upload(client, "apps", "win", "win.zip", max_bytes=2_147_483_648)

        client.remove_object.assert_called_once_with("apps", "standalone-templates/win.zip")

    def test_the_stale_local_cache_is_dropped(self) -> None:
        """The bundler validates its cache by size; same size would slip past."""
        client = MagicMock()
        client.stat_object.return_value = _stat(1000)

        with patch("pathlib.Path.unlink") as unlink:
            finalize_template_upload(client, "apps", "linux", "linux.zip")

        unlink.assert_called_once()

    def test_a_cache_that_cannot_be_removed_does_not_fail_the_upload(self) -> None:
        client = MagicMock()
        client.stat_object.return_value = _stat(1000)

        with patch("pathlib.Path.unlink", side_effect=OSError("read-only")):
            meta = finalize_template_upload(client, "apps", "linux", "linux.zip")

        assert meta.file_size == 1000

    def test_the_cache_path_matches_the_one_the_bundler_reads(self) -> None:
        assert (TEMPLATE_CACHE_DIR / "standalone-templates_win.zip").name == (
            "standalone-templates/win.zip".replace("/", "_")
        )
