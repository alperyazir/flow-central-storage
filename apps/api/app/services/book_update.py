"""Update a published book's files in place, leaving its AI output alone.

Every other upload path clears the book's whole prefix first, which throws
away ``ai-data/`` and ``ai-content/`` (hours of LLM and TTS work) and the
nested ``additional-resources/`` children. This one only writes what the
archive carries, never touches the protected folders, and deletes stale files
only when asked to.
"""

from __future__ import annotations

import json
import logging
import os
import zipfile
from dataclasses import dataclass, field

from minio import Minio

from app.services.storage import (
    UploadError,
    _detect_root_folder,
    _normalize_filename,
    iter_zip_entries,
    upload_book_archive,
)

logger = logging.getLogger(__name__)

# Top-level folders of a book that this path never writes or deletes.
PROTECTED_PREFIXES = ("ai-data/", "ai-content/", "additional-resources/")

# The only file the AI pipeline reads (services/pdf/service.py).
AI_SOURCE_PDF = "raw/original.pdf"


def is_protected(rel_path: str) -> bool:
    return rel_path.lower().startswith(PROTECTED_PREFIXES)


@dataclass
class StoredObject:
    etag: str | None
    size: int


@dataclass
class BookFilesUpdate:
    """What an update did — or, on a dry run, would do."""

    prefix: str
    dry_run: bool
    prune: bool
    root_folder: str | None
    written: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    renamed: list[dict[str, str]] = field(default_factory=list)
    # Existing keys overwritten with different bytes. Media is served with
    # a long immutable cache, so these may need a CDN purge.
    replaced_same_name: list[str] = field(default_factory=list)
    prune_candidates: list[str] = field(default_factory=list)
    pruned: list[str] = field(default_factory=list)
    protected_kept: int = 0
    ai_stale: bool = False
    # config.json at the book root: the archive is the whole book, so pruning
    # is allowed.
    full_archive: bool = False
    config_book_title: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "prefix": self.prefix,
            "dry_run": self.dry_run,
            "prune": self.prune,
            "root_folder": self.root_folder,
            "counts": {
                "written": len(self.written),
                "unchanged": len(self.unchanged),
                "renamed": len(self.renamed),
                "replaced_same_name": len(self.replaced_same_name),
                "prune_candidates": len(self.prune_candidates),
                "pruned": len(self.pruned),
                "protected_kept": self.protected_kept,
            },
            "written": self.written,
            "unchanged": self.unchanged,
            "renamed": self.renamed,
            "replaced_same_name": self.replaced_same_name,
            "prune_candidates": self.prune_candidates,
            "pruned": self.pruned,
            "ai_stale": self.ai_stale,
            "full_archive": self.full_archive,
            "config_book_title": self.config_book_title,
            "metadata": self.metadata,
        }


def list_stored_objects(client: Minio, bucket: str, prefix: str) -> dict[str, StoredObject]:
    """``{relative_path: StoredObject}`` for everything under ``prefix``."""
    stored: dict[str, StoredObject] = {}
    for obj in client.list_objects(bucket, prefix=prefix, recursive=True):
        rel = obj.object_name[len(prefix):]
        if not rel or rel.endswith("/"):
            continue
        etag = (obj.etag or "").strip('"') or None
        stored[rel] = StoredObject(etag=etag, size=obj.size or 0)
    return stored


def _root_to_strip(archive: zipfile.ZipFile, stored: dict[str, StoredObject]) -> str | None:
    """The archive's single root folder, unless it is one of the book's own folders.

    A zip of the whole book folder (``Glory_Trio_3/...``) has the book as its
    root and it must be stripped. A partial zip holding only ``assets/`` also
    has a single root, but that one is content and must be kept.
    """
    root = _detect_root_folder(archive)
    if root is None:
        return None
    top_level = {rel.split("/", 1)[0].lower() for rel in stored if "/" in rel}
    if root.lower() in top_level or _normalize_filename(root).lower() in top_level:
        return None
    return root


def _check_paths(entries: list[tuple[zipfile.ZipInfo, str]]) -> list[str]:
    """Normalized relative paths of the archive, or UploadError on any unsafe one."""
    violations: list[str] = []
    paths: list[str] = []
    for entry, final_path in entries:
        segments = final_path.split("/")
        if final_path.startswith("/") or any(seg in ("", ".", "..") for seg in segments):
            violations.append(f"{entry.filename}: path escapes the book folder")
            continue
        normalized = _normalize_filename(final_path)
        if any(not seg for seg in normalized.split("/")):
            violations.append(f"{entry.filename}: path escapes the book folder")
            continue
        if is_protected(normalized):
            violations.append(f"{entry.filename}: {normalized.split('/', 1)[0]}/ is protected")
            continue
        paths.append(normalized)
    if violations:
        shown = "; ".join(violations[:20])
        more = f" (+{len(violations) - 20} more)" if len(violations) > 20 else ""
        raise UploadError(f"Archive has entries this update may not write: {shown}{more}")
    return paths


def _read_json(client: Minio, bucket: str, key: str) -> object | None:
    try:
        response = client.get_object(bucket, key)
        try:
            return json.loads(response.read())
        finally:
            response.close()
            response.release_conn()
    except Exception as exc:  # noqa: BLE001 - a bad stored JSON only weakens the image filter
        logger.warning("Update: could not read stored %s: %s", key, exc)
        return None


def book_metadata_from_config(config: dict) -> dict[str, object]:
    """The DB fields an upload derives from config.json."""
    # Imported here: the router module imports this one.
    from app.routers.books import _collect_activity_details, _count_activities

    cover = config.get("book_cover")
    return {
        "activity_count": _count_activities(config),
        "activity_details": _collect_activity_details(config),
        "book_cover": _normalize_filename(os.path.basename(cover)) if cover else None,
    }


def update_book_files(
    *,
    client: Minio,
    bucket: str,
    prefix: str,
    book_name: str,
    archive_path: str,
    prune: bool = False,
    dry_run: bool = True,
) -> BookFilesUpdate:
    """Write the archive's files over the book at ``prefix``.

    - ``ai-data/``, ``ai-content/`` and ``additional-resources/`` are never
      written or deleted; an archive that carries them is rejected.
    - Files whose final bytes match the stored etag (single-part MD5 or
      multipart) are skipped.
    - Stored files the archive no longer has are reported as prune
      candidates and deleted only with ``prune`` and not ``dry_run``. Pruning
      needs a full book archive (config.json at the book root).
    - ``ai_stale`` is set when raw/original.pdf is replaced or removed; the AI
      pipeline is never started from here.
    """
    stored = list_stored_objects(client, bucket, prefix)
    content = {rel: obj for rel, obj in stored.items() if not is_protected(rel)}

    try:
        archive = zipfile.ZipFile(archive_path, "r")
    except zipfile.BadZipFile as exc:
        raise UploadError("Uploaded file is not a valid ZIP archive") from exc
    with archive:
        root = _root_to_strip(archive, stored)
        archive_paths = _check_paths(list(iter_zip_entries(archive, strip_root=root)))

    if not archive_paths:
        raise UploadError("Archive has no files to write")
    archive_set = set(archive_paths)
    if prune and "config.json" not in archive_set:
        raise UploadError("prune needs the full book archive, with config.json at the book root")

    # A partial archive leaves stored files in place: config references to them
    # must still resolve, and images only the stored JSON mentions must not be
    # dropped by the unreferenced-image filter. With prune the archive is the
    # whole book, so nothing outside it counts.
    extra_known: list[str] = []
    extra_docs: list[object] = []
    if not prune:
        extra_known = [rel for rel in content if rel not in archive_set]
        for rel in extra_known:
            if rel.lower().endswith(".json"):
                doc = _read_json(client, bucket, f"{prefix}{rel}")
                if doc is not None:
                    extra_docs.append(doc)

    capture: dict[str, bytes | None] = {"config.json": None}
    manifest = upload_book_archive(
        client=client,
        archive_path=archive_path,
        bucket=bucket,
        object_prefix=prefix,
        content_type="application/octet-stream",
        strip_root_folder=root is not None,
        book_name=book_name,
        extra_known_paths=extra_known,
        extra_json_documents=extra_docs,
        existing_etags={f"{prefix}{rel}": obj.etag for rel, obj in content.items() if obj.etag},
        dry_run=dry_run,
        capture=capture,
    )

    result = BookFilesUpdate(
        prefix=prefix, dry_run=dry_run, prune=prune, root_folder=root, full_archive="config.json" in archive_set
    )
    result.protected_kept = len(stored) - len(content)
    final_sizes = {rel: obj.size for rel, obj in content.items()}
    for item in manifest:
        rel = str(item["path"])[len(prefix):]
        final_sizes[rel] = int(item["stored_size"])
        if item["action"] == "unchanged":
            result.unchanged.append(rel)
        else:
            result.written.append(rel)
            if rel in content:
                result.replaced_same_name.append(rel)
        if "source" in item:
            result.renamed.append({"from": str(item["source"]), "to": rel})

    written = {str(item["path"])[len(prefix):] for item in manifest}
    result.prune_candidates = sorted(rel for rel in content if rel not in written)
    if prune:
        for rel in result.prune_candidates:
            final_sizes.pop(rel, None)
            if not dry_run:
                client.remove_object(bucket, f"{prefix}{rel}")
                result.pruned.append(rel)

    removed = prune and AI_SOURCE_PDF in result.prune_candidates
    result.ai_stale = AI_SOURCE_PDF in result.written or removed

    config_bytes = capture["config.json"]
    config: object | None
    if config_bytes is not None:
        config = json.loads(config_bytes)
    elif "config.json" in content:
        config = _read_json(client, bucket, f"{prefix}config.json")
    else:
        config = None
    metadata: dict[str, object] = {"total_size": sum(final_sizes.values())}
    if isinstance(config, dict):
        metadata.update(book_metadata_from_config(config))
        title = config.get("book_title")
        result.config_book_title = title if isinstance(title, str) else None
    result.metadata = metadata

    logger.info(
        "Update %s%s: %d written (%d replaced same name), %d unchanged, %d prune candidates, %d pruned, ai_stale=%s",
        prefix,
        " (dry run)" if dry_run else "",
        len(result.written),
        len(result.replaced_same_name),
        len(result.unchanged),
        len(result.prune_candidates),
        len(result.pruned),
        result.ai_stale,
    )
    return result
