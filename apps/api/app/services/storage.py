"""High-level storage helpers for uploading content to MinIO."""

from __future__ import annotations

import io
import logging
import os
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Iterable

from minio import Minio
from minio.commonconfig import CopySource
from minio.error import S3Error


class UploadError(Exception):
    """Raised when an upload archive cannot be processed."""


class UploadConflictError(UploadError):
    """Raised when an upload targets an existing version without override."""

    def __init__(self, version: str) -> None:
        super().__init__(f"Version '{version}' already exists; re-run with override to replace it.")
        self.version = version


class RelocationError(Exception):
    """Raised when stored objects cannot be moved between buckets."""


class RestorationError(Exception):
    """Raised when objects cannot be restored from the trash bucket."""


class TrashDeletionError(Exception):
    """Raised when permanent deletion from the trash bucket fails."""


class TrashRetentionError(TrashDeletionError):
    """Raised when attempting to delete an entry before the retention window elapses."""


class TrashEntryNotFoundError(TrashDeletionError):
    """Raised when a requested trash entry has no underlying objects."""


logger = logging.getLogger(__name__)

_VERSION_FILE_PATH = "data/version"
_VERSION_PATTERN = re.compile(r"^v?(?:0|[1-9]\d*)(?:\.(?:0|[1-9]\d*)){1,2}(?:[-+][0-9A-Za-z\-.]+)?$")
_MAX_VERSION_LENGTH = 64


@dataclass(slots=True)
class RelocationReport:
    """Summary of a MinIO relocation operation."""

    source_bucket: str
    destination_bucket: str
    source_prefix: str
    destination_prefix: str
    objects_moved: int


@dataclass(slots=True)
class TrashEntry:
    """Aggregated metadata for a prefix stored in the trash bucket."""

    key: str
    bucket: str
    path: str
    item_type: str
    object_count: int
    total_size: int
    metadata: dict[str, str] | None = None
    youngest_last_modified: datetime | None = None
    eligible_at: datetime | None = None
    eligible_for_deletion: bool = False


@dataclass(slots=True)
class DeletionReport:
    """Summary of a permanent deletion from the trash bucket."""

    trash_bucket: str
    key: str
    objects_removed: int


def _detect_root_folder(archive: zipfile.ZipFile) -> str | None:
    """Detect if ZIP contains a single root folder and return its name."""

    root_folders = set()

    for entry in archive.infolist():
        normalized_path = entry.filename.replace("\\", "/").rstrip("/")

        # Skip macOS metadata
        if "/__MACOSX/" in normalized_path or normalized_path.startswith("__MACOSX/"):
            continue
        if os.path.basename(normalized_path) == ".DS_Store":
            continue
        if os.path.basename(normalized_path).startswith("._"):
            continue

        # Get root folder
        parts = normalized_path.split("/")
        if len(parts) > 0:
            root_folders.add(parts[0])

        # If more than one root folder, no single root exists
        if len(root_folders) > 1:
            return None

    # Return the single root folder if exactly one exists
    return root_folders.pop() if len(root_folders) == 1 else None


_ZIP_MAX_ENTRY_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB per file
_ZIP_MAX_TOTAL_SIZE = 20 * 1024 * 1024 * 1024  # 20 GB total extracted


def iter_zip_entries(archive: zipfile.ZipFile, strip_root: str | None = None) -> Iterable[tuple[zipfile.ZipInfo, str]]:
    """Yield file entries from archive with optionally stripped paths.

    Returns tuples of (entry, final_path) where final_path has the root folder stripped if specified.
    """

    total_size = 0
    for entry in archive.infolist():
        if entry.is_dir():
            continue

        # Normalize path for consistent checking
        normalized_path = entry.filename.replace("\\", "/")

        # Skip __MACOSX folders (macOS resource forks)
        if "/__MACOSX/" in normalized_path or normalized_path.startswith("__MACOSX/"):
            continue

        # Skip .DS_Store files (macOS folder metadata)
        if os.path.basename(normalized_path) == ".DS_Store":
            continue

        # Skip macOS resource fork files (._*)
        if os.path.basename(normalized_path).startswith("._"):
            continue

        # Skip backup and temporary files
        basename_lower = os.path.basename(normalized_path).lower()
        if basename_lower.endswith((".fbinf", ".bak", ".tmp")):
            logger.debug("Skipping backup/temp file: %s", entry.filename)
            continue

        # SEC-C2: Reject oversized entries to mitigate zip-bomb attacks
        if entry.file_size > _ZIP_MAX_ENTRY_SIZE:
            raise UploadError(
                f"Entry '{entry.filename}' exceeds the 2 GB per-file limit (declared size: {entry.file_size} bytes)"
            )
        total_size += entry.file_size
        if total_size > _ZIP_MAX_TOTAL_SIZE:
            raise UploadError(f"Total extracted size exceeds the 20 GB limit (accumulated: {total_size} bytes)")

        # Strip root folder if specified
        final_path = normalized_path
        if strip_root and normalized_path.startswith(f"{strip_root}/"):
            final_path = normalized_path[len(strip_root) + 1 :]

        yield entry, final_path


def upload_book_archive(
    *,
    client: Minio,
    archive_bytes: bytes,
    bucket: str,
    object_prefix: str,
    content_type: str | None = None,
    strip_root_folder: bool = True,
) -> list[dict[str, object]]:
    """Upload the provided ZIP archive into MinIO under the given prefix.

    If strip_root_folder is True and ZIP contains a single root folder, that folder is stripped.
    For example: BRAINS/file.txt becomes file.txt in storage.

    Returns a manifest containing uploaded file paths and sizes.
    """

    try:
        archive = zipfile.ZipFile(io.BytesIO(archive_bytes))
    except zipfile.BadZipFile as exc:  # pragma: no cover - handled in tests
        raise UploadError("Uploaded file is not a valid ZIP archive") from exc

    # Detect and strip root folder if requested
    root_to_strip = None
    if strip_root_folder:
        root_to_strip = _detect_root_folder(archive)

    manifest: list[dict[str, object]] = []
    for entry, final_path in iter_zip_entries(archive, strip_root=root_to_strip):
        file_path = f"{object_prefix}{final_path}"
        with archive.open(entry) as file_obj:
            stream = io.BytesIO(file_obj.read())
            stream.seek(0)
            client.put_object(
                bucket,
                file_path,
                stream,
                length=entry.file_size,
                content_type=content_type or "application/octet-stream",
            )
        manifest.append({"path": file_path, "size": entry.file_size})

    return manifest


def upload_app_archive(
    *,
    client: Minio,
    archive_bytes: bytes,
    bucket: str,
    platform: str,
    version: str,
    content_type: str | None = None,
) -> list[dict[str, object]]:
    """Upload an application build archive into MinIO under platform/version."""

    prefix = f"{platform}/{version}/"
    return upload_book_archive(
        client=client,
        archive_bytes=archive_bytes,
        bucket=bucket,
        object_prefix=prefix,
        content_type=content_type,
    )


def extract_manifest_version(archive_bytes: bytes) -> str:
    """Return the version string declared in ``data/version`` within the archive."""

    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            version_path = _locate_version_entry(archive)
            logger.debug("version_path: %s", version_path)
            if version_path is None:
                raise UploadError("Archive is missing required data/version file")

            try:
                with archive.open(version_path) as file_handle:
                    raw_value = file_handle.read().decode("utf-8").strip()
            except UnicodeDecodeError as exc:
                raise UploadError("data/version must be UTF-8 encoded") from exc
    except zipfile.BadZipFile as exc:
        raise UploadError("Uploaded file is not a valid ZIP archive") from exc

    if not raw_value:
        raise UploadError("data/version must contain a version value")

    if len(raw_value) > _MAX_VERSION_LENGTH:
        raise UploadError("data/version exceeds the maximum length of 64 characters")

    if not _VERSION_PATTERN.match(raw_value):
        raise UploadError("data/version must use semantic versioning (e.g., 1.2.3 or 1.2.3-beta)")

    return raw_value


def ensure_version_target(
    *,
    client: Minio,
    bucket: str,
    prefix: str,
    version: str,
    override: bool,
) -> bool:
    """Ensure the ``prefix`` is available for writing, handling conflicts.

    Returns ``True`` if an existing prefix was detected (callers may remove it when overriding).
    """

    try:
        exists = _prefix_exists(client, bucket, prefix)
    except S3Error as exc:  # pragma: no cover - propagated to caller
        logger.error(
            "Failed to validate existing objects for prefix '%s' in bucket '%s': %s",
            prefix,
            bucket,
            exc,
        )
        raise UploadError("Unable to inspect storage for existing version") from exc

    if exists and not override:
        raise UploadConflictError(version)

    return exists


# def _locate_version_entry(archive: zipfile.ZipFile) -> str | None:
#     """Return the archive member path for ``data/version`` if present."""

#     target =  archive.namelist()[0] +_VERSION_FILE_PATH.lower()
#     print("1", target)
#     print("2", archive.namelist())
#     for name in archive.namelist():
#         print("3", name)
#         normalized = name.replace("\\", "/").rstrip("/")
#         if normalized.lower() == target:
#             return name
#     return None


def _locate_version_entry(archive: zipfile.ZipFile) -> str | None:
    """
    Return the archive member path for 'data/version' if present.
    Supports both:
      - data/version
      - <any_top_folder>/data/version
    Skips macOS resource entries like __MACOSX and ._* files.
    """

    def norm(p: str) -> str:
        return p.replace("\\", "/").rstrip("/")

    candidates: list[str] = []

    for name in archive.namelist():
        # Exact name from namelist -> safe for getinfo
        info = archive.getinfo(name)

        # Skip directories
        if hasattr(info, "is_dir") and info.is_dir():
            continue

        n = norm(name)
        ln = n.lower()

        # Skip macOS resource forks and __MACOSX
        if ln.startswith("__macosx/"):
            continue
        if os.path.basename(n).startswith("._"):
            continue

        # Match data/version at root or under any top-level folder
        if ln == "data/version" or ln.endswith("/data/version"):
            candidates.append(name)

    if not candidates:
        return None

    # Prefer the "simplest" path (fewest segments, then shortest length)
    def key_fn(p: str):
        parts = norm(p).split("/")
        return (len(parts), len(p))

    candidates.sort(key=key_fn)
    chosen = candidates[0]
    logger.debug("Matched version entry: %s", chosen)
    return chosen


def _prefix_exists(client: Minio, bucket: str, prefix: str) -> bool:
    """Return True if at least one object exists under ``prefix`` within ``bucket``."""

    objects = client.list_objects(bucket, prefix=prefix, recursive=True)
    for obj in objects:
        if obj.object_name:
            return True
    return False


def list_objects_tree(client: Minio, bucket: str, prefix: str) -> dict[str, object]:
    """Return a hierarchical tree of objects under ``prefix`` within ``bucket``."""

    root = {
        "path": prefix,
        "type": "folder",
        "children": {},
    }

    objects = client.list_objects(bucket, prefix=prefix, recursive=True)
    for obj in objects:
        rel_path = obj.object_name[len(prefix) :]
        parts = [p for p in rel_path.split("/") if p]
        current = root

        for part in parts[:-1]:
            children = current.setdefault("children", {})
            if part not in children:
                children[part] = {
                    "path": f"{current['path']}{part}/",
                    "type": "folder",
                    "children": {},
                }
            current = children[part]

        if not parts:
            continue

        file_name = parts[-1]
        children = current.setdefault("children", {})
        children[file_name] = {
            "path": f"{current['path']}{file_name}",
            "type": "file",
            "size": obj.size,
        }

    return _normalize_tree(root)


def _normalize_tree(node: dict[str, object]) -> dict[str, object]:
    children = node.get("children")
    if not children:
        node["children"] = []
        return node

    normalized_children = []
    for name, child in children.items():
        normalized_children.append(_normalize_tree(child))
    node["children"] = sorted(normalized_children, key=lambda item: item["path"])
    return node


def _normalize_timestamp(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def list_trash_entries(
    client: Minio,
    trash_bucket: str,
    retention: timedelta | None = None,
) -> list[TrashEntry]:
    """Aggregate trash bucket contents into logical restore targets."""

    aggregates: dict[str, TrashEntry] = {}

    try:
        objects = client.list_objects(trash_bucket, recursive=True)
    except S3Error as exc:  # pragma: no cover - propagated to caller
        logger.error("Failed listing trash bucket '%s': %s", trash_bucket, exc)
        raise RelocationError(f"Unable to list trash bucket '{trash_bucket}'") from exc

    for obj in objects:
        object_name = obj.object_name
        if not object_name or object_name.endswith("/"):
            continue

        parts = [segment for segment in object_name.split("/") if segment]
        if not parts:
            continue

        bucket = parts[0]
        item_type = "unknown"
        prefix_parts: list[str]
        metadata: dict[str, str] | None = None

        if bucket == "publishers" and len(parts) >= 4 and parts[2] == "books":
            item_type = "book"
            prefix_parts = parts[1:4]  # [publisher, "books", book_name]
            metadata = {"publisher": parts[1], "book_name": parts[3]}
        elif bucket == "publishers" and len(parts) >= 4 and parts[2] == "assets":
            item_type = "publisher_asset"
            prefix_parts = parts[1:4]  # [publisher, "assets", asset_type]
            metadata = {"publisher": parts[1], "asset_type": parts[3]}
        elif bucket == "apps" and len(parts) >= 3:
            item_type = "app"
            prefix_parts = parts[1:3]
            metadata = {"platform": parts[1], "version": parts[2]}
        elif bucket == "teachers" and len(parts) >= 3 and parts[2] == "materials":
            item_type = "teacher_material"
            prefix_parts = parts[1:3]  # [teacher_id, "materials"]
            metadata = {"teacher_id": parts[1]}
        else:
            # Fallback: treat the next segment as the identifier when available.
            prefix_parts = parts[1:2] if len(parts) >= 2 else []

        if not prefix_parts:
            continue

        key_prefix = "/".join([bucket, *prefix_parts])
        aggregate = aggregates.get(key_prefix)
        if aggregate is None:
            aggregates[key_prefix] = TrashEntry(
                key=f"{key_prefix}/",
                bucket=bucket,
                path="/".join(prefix_parts),
                item_type=item_type,
                object_count=0,
                total_size=0,
                metadata=metadata,
            )
            aggregate = aggregates[key_prefix]
        else:
            # Preserve metadata from the first encountered object.
            if aggregate.metadata is None and metadata:
                aggregate.metadata = metadata

        last_modified = _normalize_timestamp(getattr(obj, "last_modified", None))
        if last_modified is not None:
            if aggregate.youngest_last_modified is None or last_modified > aggregate.youngest_last_modified:
                aggregate.youngest_last_modified = last_modified

        aggregate.object_count += 1
        aggregate.total_size += obj.size or 0

    retention_window = retention if retention is not None else timedelta(0)
    now = datetime.now(UTC)
    for aggregate in aggregates.values():
        if aggregate.youngest_last_modified is not None:
            aggregate.eligible_at = aggregate.youngest_last_modified + retention_window
            aggregate.eligible_for_deletion = aggregate.eligible_at <= now
        else:
            aggregate.eligible_at = None if retention_window > timedelta(0) else now
            aggregate.eligible_for_deletion = retention_window <= timedelta(0)

    return sorted(aggregates.values(), key=lambda entry: entry.key)


def move_prefix_to_trash(
    *,
    client: Minio,
    source_bucket: str,
    prefix: str,
    trash_bucket: str,
) -> RelocationReport:
    """Move all objects under ``prefix`` into the trash bucket while preserving paths."""

    normalized_prefix = prefix if prefix.endswith("/") else f"{prefix}/"
    destination_prefix = f"{source_bucket}/{normalized_prefix}"

    try:
        objects = list(client.list_objects(source_bucket, prefix=normalized_prefix, recursive=True))
    except S3Error as exc:  # pragma: no cover - network/MinIO failure
        logger.error("Failed listing objects for prefix '%s/%s': %s", source_bucket, normalized_prefix, exc)
        raise RelocationError(f"Unable to list objects for prefix '{normalized_prefix}'") from exc

    moved = 0
    for obj in objects:
        source_object = obj.object_name
        relative_path = source_object[len(normalized_prefix) :]
        destination_object = f"{destination_prefix}{relative_path}"

        try:
            client.copy_object(
                trash_bucket,
                destination_object,
                CopySource(source_bucket, source_object),
            )
            client.remove_object(source_bucket, source_object)
        except S3Error as exc:  # pragma: no cover - depends on MinIO responses
            logger.error(
                "Failed relocating object '%s/%s' to '%s/%s': %s",
                source_bucket,
                source_object,
                trash_bucket,
                destination_object,
                exc,
            )
            raise RelocationError(f"Unable to relocate object '{source_object}'") from exc
        moved += 1

    report = RelocationReport(
        source_bucket=source_bucket,
        destination_bucket=trash_bucket,
        source_prefix=normalized_prefix,
        destination_prefix=destination_prefix,
        objects_moved=moved,
    )

    logger.info(
        "Relocated %s objects from %s/%s to %s/%s",
        moved,
        report.source_bucket,
        report.source_prefix,
        report.destination_bucket,
        report.destination_prefix,
    )
    return report


def restore_prefix_from_trash(
    *,
    client: Minio,
    trash_bucket: str,
    key: str,
) -> RelocationReport:
    """Restore a previously soft-deleted prefix from the trash bucket."""

    normalized_key = key if key.endswith("/") else f"{key}/"
    parts = [segment for segment in normalized_key.split("/") if segment]
    if len(parts) < 2:
        raise RestorationError("Invalid trash key; expected bucket/prefix pairs")

    destination_bucket = parts[0]
    destination_prefix = "/".join(parts[1:])
    if destination_prefix and not destination_prefix.endswith("/"):
        destination_prefix = f"{destination_prefix}/"

    try:
        objects = list(client.list_objects(trash_bucket, prefix=normalized_key, recursive=True))
    except S3Error as exc:  # pragma: no cover - depends on MinIO responses
        logger.error("Failed listing trash objects for '%s': %s", normalized_key, exc)
        raise RestorationError(f"Unable to list trash entry '{normalized_key}'") from exc

    if not objects:
        raise RestorationError(f"No trash objects found for key '{normalized_key}'")

    restored = 0
    for obj in objects:
        source_object = obj.object_name
        relative_path = source_object[len(normalized_key) :]
        if relative_path == "":  # Defensive guard for prefix placeholders
            continue

        destination_object = f"{destination_prefix}{relative_path}"

        try:
            client.copy_object(
                destination_bucket,
                destination_object,
                CopySource(trash_bucket, source_object),
            )
            client.remove_object(trash_bucket, source_object)
        except S3Error as exc:  # pragma: no cover - depends on MinIO responses
            logger.error(
                "Failed restoring object '%s' to '%s/%s': %s",
                source_object,
                destination_bucket,
                destination_object,
                exc,
            )
            raise RestorationError(f"Unable to restore object '{source_object}'") from exc
        restored += 1

    report = RelocationReport(
        source_bucket=trash_bucket,
        destination_bucket=destination_bucket,
        source_prefix=normalized_key,
        destination_prefix=destination_prefix,
        objects_moved=restored,
    )

    logger.info(
        "Restored %s objects from %s/%s to %s/%s",
        restored,
        report.source_bucket,
        report.source_prefix,
        report.destination_bucket,
        report.destination_prefix,
    )

    return report


def delete_prefix_from_trash(
    *,
    client: Minio,
    trash_bucket: str,
    key: str,
    retention: timedelta,
    force: bool = False,
    override_reason: str | None = None,
) -> DeletionReport:
    """Permanently delete a trash entry if it satisfies the retention policy."""

    normalized_key = key if key.endswith("/") else f"{key}/"

    try:
        objects = list(client.list_objects(trash_bucket, prefix=normalized_key, recursive=True))
    except S3Error as exc:  # pragma: no cover - depends on MinIO responses
        logger.error("Failed listing trash objects for deletion '%s': %s", normalized_key, exc)
        raise TrashDeletionError(f"Unable to list trash entry '{normalized_key}'") from exc

    if not objects:
        raise TrashEntryNotFoundError(f"No trash objects found for key '{normalized_key}'")

    now = datetime.now(UTC)
    if not force:
        youngest: datetime | None = None
        for obj in objects:
            modified = getattr(obj, "last_modified", None)
            if modified is None:
                continue
            if youngest is None or modified > youngest:
                youngest = modified

        if youngest is not None and now - youngest < retention:
            logger.warning(
                "Deletion blocked for '%s'; youngest object age %s below retention %s",
                normalized_key,
                now - youngest,
                retention,
            )
            raise TrashRetentionError("Trash entry is still within the mandatory retention window")

    removed = 0
    for obj in objects:
        object_name = obj.object_name
        if not object_name:
            continue
        try:
            client.remove_object(trash_bucket, object_name)
        except S3Error as exc:  # pragma: no cover - depends on MinIO responses
            logger.error(
                "Failed removing trash object '%s/%s': %s",
                trash_bucket,
                object_name,
                exc,
            )
            raise TrashDeletionError(f"Unable to remove trash object '{object_name}'") from exc
        removed += 1

    logger.info(
        "Deleted %s trash objects under %s/%s (force=%s, override_reason=%s)",
        removed,
        trash_bucket,
        normalized_key,
        force,
        override_reason if force else None,
    )

    return DeletionReport(trash_bucket=trash_bucket, key=normalized_key, objects_removed=removed)
