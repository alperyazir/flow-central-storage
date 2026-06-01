"""Tests for standalone-app template/bundle version stamping."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.services.standalone_apps import (
    APP_VERSION_META_KEY,
    _extract_version_from_metadata,
    get_template_version,
    upload_template,
)


def test_extract_version_handles_amz_prefix_and_case() -> None:
    assert _extract_version_from_metadata({"x-amz-meta-app-version": "1.5.1"}) == "1.5.1"
    assert _extract_version_from_metadata({"X-Amz-Meta-App-Version": "2.0.0"}) == "2.0.0"
    assert _extract_version_from_metadata({"app-version": "3.1"}) == "3.1"


def test_extract_version_missing_or_empty() -> None:
    assert _extract_version_from_metadata(None) is None
    assert _extract_version_from_metadata({}) is None
    assert _extract_version_from_metadata({"content-type": "application/zip"}) is None
    assert _extract_version_from_metadata({"x-amz-meta-app-version": ""}) is None


def test_upload_template_stamps_version_metadata() -> None:
    client = MagicMock()
    meta = upload_template(
        client=client,
        bucket="apps",
        platform="mac",
        file_data=b"zipdata",
        file_name="mac.zip",
        version=" 1.5.1 ",
    )
    # Stored (and returned) version is trimmed
    assert meta.version == "1.5.1"
    assert client.put_object.call_args.kwargs["metadata"] == {APP_VERSION_META_KEY: "1.5.1"}


def test_upload_template_without_version_sends_no_metadata() -> None:
    client = MagicMock()
    meta = upload_template(
        client=client,
        bucket="apps",
        platform="win",
        file_data=b"zipdata",
        file_name="win.zip",
        version=None,
    )
    assert meta.version is None
    assert client.put_object.call_args.kwargs["metadata"] is None


def test_get_template_version_reads_stat_metadata() -> None:
    client = MagicMock()
    client.stat_object.return_value = MagicMock(metadata={"x-amz-meta-app-version": "4.2.0"})
    assert get_template_version(client, "apps", "linux") == "4.2.0"


def test_get_template_version_none_when_unstamped() -> None:
    client = MagicMock()
    client.stat_object.return_value = MagicMock(metadata={"content-type": "application/zip"})
    assert get_template_version(client, "apps", "linux") is None
