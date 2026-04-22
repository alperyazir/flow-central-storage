"""Service layer for standalone app templates and bundling."""

from __future__ import annotations

import io
import logging
import os
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from minio import Minio
from minio.error import S3Error

logger = logging.getLogger(__name__)

TEMPLATE_PREFIX = "standalone-templates"
BUNDLE_PREFIX = "bundles"
ALLOWED_PLATFORMS = {"mac", "win", "win7-8", "linux"}
PRESIGNED_URL_EXPIRY_SECONDS = 21600  # 6 hours
TEMPLATE_CACHE_DIR = Path(tempfile.gettempdir()) / "fcs_template_cache"
ASSET_DOWNLOAD_WORKERS = 8  # concurrent R2 downloads

_BUNDLE_JUNK_BASENAMES = {".ds_store", "desktop.ini", ".keep", ".gitkeep", "settings.json"}
_BUNDLE_JUNK_SUFFIXES = (".fbinf", ".bak", ".tmp")

# Top-level folders inside a book's R2 prefix that must never end up in a
# bundle. These hold content that is either orthogonal to the flowbook
# runtime (AI-generated artifacts) or is download-only data (raw PDFs
# for child resources, v1 additional-resources layout).
_BUNDLE_SKIP_PREFIXES: tuple[str, ...] = (
    "raw/",
    "additional-resources/",
    "ai-data/",
    "ai-content/",
)


def should_skip_bundled_path(relative_path: str) -> bool:
    """Return True for paths that must be excluded from a standalone bundle.

    Filters macOS metadata, backup/scratch files, AI-processing artifacts
    and additional-resource layouts. The path is the object name relative
    to the book's R2 prefix (e.g. ``raw/foo.pdf``, ``ai-data/x.json``).
    """
    if not relative_path:
        return True

    norm = relative_path.replace("\\", "/").lstrip("/")
    if not norm:
        return True

    if "__MACOSX/" in norm or norm.startswith("__MACOSX/"):
        return True

    basename = norm.rsplit("/", 1)[-1]
    if basename.startswith("._"):
        return True

    bn_lower = basename.lower()
    if bn_lower in _BUNDLE_JUNK_BASENAMES:
        return True
    if bn_lower.endswith(_BUNDLE_JUNK_SUFFIXES):
        return True

    for prefix in _BUNDLE_SKIP_PREFIXES:
        if norm.startswith(prefix):
            return True

    return False


class TemplateNotFoundError(Exception):
    """Raised when a requested template does not exist."""

    pass


class InvalidPlatformError(Exception):
    """Raised when an invalid platform is specified."""

    pass


class BundleCreationError(Exception):
    """Raised when bundle creation fails."""

    pass


@dataclass(slots=True)
class TemplateMetadata:
    """Metadata about an uploaded template."""

    platform: str
    file_name: str
    file_size: int
    uploaded_at: datetime
    object_name: str


@dataclass(slots=True)
class BundleMetadata:
    """Metadata about a created bundle."""

    publisher_name: str
    book_name: str
    platform: str
    file_name: str
    file_size: int
    created_at: datetime
    object_name: str
    download_url: str | None = None


def _get_template_object_name(platform: str) -> str:
    """Get the MinIO object name for a template."""
    return f"{TEMPLATE_PREFIX}/{platform}.zip"


def _validate_platform(platform: str) -> str:
    """Validate and normalize platform name."""
    normalized = platform.lower()
    if normalized not in ALLOWED_PLATFORMS:
        raise InvalidPlatformError(f"Invalid platform '{platform}'. Allowed: {', '.join(sorted(ALLOWED_PLATFORMS))}")
    return normalized


def upload_template(
    client: Minio,
    bucket: str,
    platform: str,
    file_data: bytes,
    file_name: str,
) -> TemplateMetadata:
    """Upload a standalone app template to MinIO.

    Args:
        client: MinIO client instance
        bucket: Target bucket name
        platform: Platform identifier (mac, win, linux)
        file_data: Template zip file contents
        file_name: Original filename

    Returns:
        TemplateMetadata with upload information

    Raises:
        InvalidPlatformError: If platform is not supported
    """
    normalized_platform = _validate_platform(platform)
    object_name = _get_template_object_name(normalized_platform)

    data_stream = io.BytesIO(file_data)
    file_size = len(file_data)

    client.put_object(
        bucket_name=bucket,
        object_name=object_name,
        data=data_stream,
        length=file_size,
        content_type="application/zip",
    )

    # Update local cache
    TEMPLATE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = TEMPLATE_CACHE_DIR / object_name.replace("/", "_")
    cache_path.write_bytes(file_data)
    logger.info("Updated local template cache: %s", cache_path)

    logger.info(
        "Uploaded template for platform %s: %s (%d bytes)",
        normalized_platform,
        object_name,
        file_size,
    )

    return TemplateMetadata(
        platform=normalized_platform,
        file_name=file_name,
        file_size=file_size,
        uploaded_at=datetime.now(timezone.utc),
        object_name=object_name,
    )


def list_templates(
    client: Minio,
    external_client: Minio,
    bucket: str,
) -> list[TemplateMetadata]:
    """List all uploaded standalone app templates.

    Args:
        client: MinIO client for internal operations
        external_client: MinIO client for generating presigned URLs
        bucket: Bucket name containing templates

    Returns:
        List of TemplateMetadata for each uploaded template
    """
    templates = []
    prefix = f"{TEMPLATE_PREFIX}/"

    try:
        objects = client.list_objects(bucket, prefix=prefix, recursive=True)

        for obj in objects:
            # Extract platform from object name (standalone-templates/mac.zip -> mac)
            file_name = obj.object_name.split("/")[-1]
            if not file_name.endswith(".zip"):
                continue

            platform = file_name[:-4]  # Remove .zip extension
            if platform not in ALLOWED_PLATFORMS:
                continue

            templates.append(
                TemplateMetadata(
                    platform=platform,
                    file_name=file_name,
                    file_size=obj.size,
                    uploaded_at=obj.last_modified,
                    object_name=obj.object_name,
                )
            )

    except S3Error as exc:
        logger.warning("Failed to list templates: %s", exc)

    return sorted(templates, key=lambda t: t.platform)


def get_template_download_url(
    external_client: Minio,
    bucket: str,
    platform: str,
) -> str:
    """Generate a presigned URL for downloading a template.

    Args:
        external_client: MinIO client configured for external access
        bucket: Bucket name
        platform: Platform identifier

    Returns:
        Presigned download URL

    Raises:
        InvalidPlatformError: If platform is not supported
        TemplateNotFoundError: If template does not exist
    """
    normalized_platform = _validate_platform(platform)
    object_name = _get_template_object_name(normalized_platform)

    try:
        url = external_client.presigned_get_object(
            bucket_name=bucket,
            object_name=object_name,
            expires=timedelta(seconds=PRESIGNED_URL_EXPIRY_SECONDS),
        )
        return url
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            raise TemplateNotFoundError(f"Template for platform '{normalized_platform}' not found") from exc
        raise


def delete_template(
    client: Minio,
    bucket: str,
    platform: str,
) -> bool:
    """Delete a standalone app template.

    Args:
        client: MinIO client
        bucket: Bucket name
        platform: Platform identifier

    Returns:
        True if deleted successfully

    Raises:
        InvalidPlatformError: If platform is not supported
        TemplateNotFoundError: If template does not exist
    """
    normalized_platform = _validate_platform(platform)
    object_name = _get_template_object_name(normalized_platform)

    # Check if exists first
    try:
        client.stat_object(bucket, object_name)
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            raise TemplateNotFoundError(f"Template for platform '{normalized_platform}' not found") from exc
        raise

    client.remove_object(bucket, object_name)
    logger.info("Deleted template for platform %s", normalized_platform)
    return True


def template_exists(client: Minio, bucket: str, platform: str) -> bool:
    """Check if a template exists for the given platform."""
    try:
        normalized_platform = _validate_platform(platform)
        object_name = _get_template_object_name(normalized_platform)
        client.stat_object(bucket, object_name)
        return True
    except (S3Error, InvalidPlatformError):
        return False


def _get_cached_template(client: Minio, bucket: str, object_name: str) -> str:
    """Download template to local cache if not already cached. Returns local path."""
    TEMPLATE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = TEMPLATE_CACHE_DIR / object_name.replace("/", "_")

    # Check if cached version is still valid (compare etag/size)
    if cache_path.exists():
        try:
            stat = client.stat_object(bucket, object_name)
            if cache_path.stat().st_size == stat.size:
                logger.debug("Template cache hit: %s", cache_path)
                return str(cache_path)
        except Exception:
            pass

    # Download to cache
    logger.info("Template cache miss, downloading: %s", object_name)
    client.fget_object(bucket, object_name, str(cache_path))
    return str(cache_path)


def _download_asset(client: Minio, bucket: str, object_name: str, dest_path: str) -> str:
    """Download a single asset from R2. Used in thread pool."""
    dest_parent = os.path.dirname(dest_path)
    if dest_parent:
        os.makedirs(dest_parent, exist_ok=True)
    client.fget_object(bucket, object_name, dest_path)
    return dest_path


def create_bundle(
    client: Minio,
    external_client: Minio,
    apps_bucket: str,
    publishers_bucket: str,
    platform: str,
    publisher_slug: str,
    book_name: str,
    force: bool = False,
    on_progress: "Callable[[int, str, str], None] | None" = None,
) -> tuple[str, str, int, datetime]:
    """Create a bundle by combining app template with book assets.

    Args:
        client: MinIO client for internal operations
        external_client: MinIO client for presigned URLs
        apps_bucket: Bucket containing app templates
        publishers_bucket: Bucket containing book assets
        platform: Target platform (mac, win, linux)
        publisher_id: Publisher ID (integer, for locating book assets)
        book_name: Book name
        force: If True, recreate bundle even if it exists

    Returns:
        Tuple of (download_url, file_name, file_size, expires_at)

    Raises:
        InvalidPlatformError: If platform is not supported
        TemplateNotFoundError: If template does not exist
        BundleCreationError: If bundle creation fails
    """
    normalized_platform = _validate_platform(platform)
    template_object_name = _get_template_object_name(normalized_platform)

    # Check template exists
    try:
        client.stat_object(apps_bucket, template_object_name)
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            raise TemplateNotFoundError(f"Template for platform '{normalized_platform}' not found") from exc
        raise

    # Check if bundle already exists (unless force=True)
    if not force:
        bundle_prefix = f"{BUNDLE_PREFIX}/{publisher_slug}/{book_name}/"
        try:
            existing_bundles = list(client.list_objects(apps_bucket, prefix=bundle_prefix, recursive=True))
            # Find bundle matching this platform
            for obj in existing_bundles:
                file_name = obj.object_name.split("/")[-1]
                # Check if this bundle is for the requested platform
                if file_name.lower().startswith(f"({normalized_platform})"):
                    logger.info(
                        "Found existing bundle for %s/%s platform %s: %s",
                        publisher_slug,
                        book_name,
                        normalized_platform,
                        obj.object_name,
                    )
                    # Return existing bundle
                    expires_at = datetime.now(timezone.utc) + timedelta(seconds=PRESIGNED_URL_EXPIRY_SECONDS)
                    download_url = external_client.presigned_get_object(
                        bucket_name=apps_bucket,
                        object_name=obj.object_name,
                        expires=timedelta(seconds=PRESIGNED_URL_EXPIRY_SECONDS),
                    )
                    return (download_url, file_name, obj.size, expires_at)
        except S3Error:
            pass  # No existing bundle, continue to create

    def _progress(pct: int, step: str, detail: str = "") -> None:
        if on_progress:
            on_progress(pct, step, detail)

    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            # 1. Get template from local cache (or download once)
            _progress(10, "template", "Loading app template...")
            template_path = _get_cached_template(client, apps_bucket, template_object_name)

            # 2. Extract template
            _progress(20, "extracting", "Extracting template...")
            extract_dir = os.path.join(temp_dir, "app")
            os.makedirs(extract_dir, exist_ok=True)

            with zipfile.ZipFile(template_path, "r") as zf:
                zf.extractall(extract_dir)

            # 3. Remove __MACOSX metadata folder if present
            macosx_dir = os.path.join(extract_dir, "__MACOSX")
            if os.path.isdir(macosx_dir):
                import shutil

                shutil.rmtree(macosx_dir)

            # 4. Find the app folder containing 'data' directory (may be nested)
            # Template structures: {platform}/({platform}) FlowBook v1.5.2/data/
            app_root = extract_dir
            app_folder_name = None

            for dirpath, dirnames, _files in os.walk(extract_dir):
                if "data" in dirnames:
                    app_root = dirpath
                    app_folder_name = os.path.basename(dirpath)
                    extract_dir = os.path.dirname(dirpath)
                    break

            # 4. Create book directory structure inside data/books/
            data_dir = os.path.join(app_root, "data")
            if not os.path.isdir(data_dir):
                os.makedirs(data_dir, exist_ok=True)

            book_dir = os.path.join(data_dir, "books", book_name)
            os.makedirs(book_dir, exist_ok=True)

            # 5. Download book assets in parallel
            _progress(30, "downloading", "Downloading book assets...")
            book_prefix = f"{publisher_slug}/books/{book_name}/"
            objects = [
                obj for obj in client.list_objects(publishers_bucket, prefix=book_prefix, recursive=True)
                if not obj.is_dir and obj.object_name[len(book_prefix):]
            ]

            download_tasks = []
            for obj in objects:
                relative_path = obj.object_name[len(book_prefix):]
                if should_skip_bundled_path(relative_path):
                    continue
                dest_path = os.path.join(book_dir, relative_path)
                download_tasks.append((publishers_bucket, obj.object_name, dest_path))

            asset_count = 0
            total_assets = len(download_tasks)
            with ThreadPoolExecutor(max_workers=ASSET_DOWNLOAD_WORKERS) as executor:
                futures = {
                    executor.submit(_download_asset, client, bucket, obj_name, dest): obj_name
                    for bucket, obj_name, dest in download_tasks
                }
                for future in as_completed(futures):
                    future.result()  # Raise on error
                    asset_count += 1
                    # Progress: 30-70% for downloads
                    if total_assets > 0:
                        pct = 30 + int((asset_count / total_assets) * 40)
                        _progress(pct, "downloading", f"{asset_count}/{total_assets} assets")

            logger.info("Downloaded %d assets in parallel for book %s/%s", asset_count, publisher_slug, book_name)

            # 6. Rename app folder to include book name and create ZIP
            if app_folder_name:
                bundle_name = f"{app_folder_name} - {book_name}"
            else:
                bundle_name = f"({normalized_platform}) FlowBook - {book_name}"

            # Rename the app folder so ZIP root matches bundle name
            if app_folder_name and app_folder_name != bundle_name:
                old_path = os.path.join(extract_dir, app_folder_name)
                new_path = os.path.join(extract_dir, bundle_name)
                os.rename(old_path, new_path)

            _progress(75, "zipping", "Creating bundle archive...")
            bundle_path = os.path.join(temp_dir, f"{bundle_name}.zip")

            with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_STORED) as zf:
                for root, _dirs, files in os.walk(extract_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, extract_dir)
                        zf.write(file_path, arcname)

            # 6. Upload bundle to R2
            _progress(85, "uploading", "Uploading bundle...")
            bundle_object_name = f"{BUNDLE_PREFIX}/{publisher_slug}/{book_name}/{bundle_name}.zip"
            bundle_size = os.path.getsize(bundle_path)

            client.fput_object(
                apps_bucket,
                bundle_object_name,
                bundle_path,
                content_type="application/zip",
            )

            # 7. Generate presigned URL
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=PRESIGNED_URL_EXPIRY_SECONDS)
            download_url = external_client.presigned_get_object(
                bucket_name=apps_bucket,
                object_name=bundle_object_name,
                expires=timedelta(seconds=PRESIGNED_URL_EXPIRY_SECONDS),
            )

            logger.info(
                "Created bundle %s for %s/%s (%d bytes)",
                bundle_name,
                publisher_slug,
                book_name,
                bundle_size,
            )

            return (download_url, f"{bundle_name}.zip", bundle_size, expires_at)

        except Exception as exc:
            logger.error("Failed to create bundle: %s", exc)
            raise BundleCreationError(f"Failed to create bundle: {exc}") from exc


def list_bundles(
    client: Minio,
    external_client: Minio,
    bucket: str,
) -> list[BundleMetadata]:
    """List all created bundles.

    Args:
        client: MinIO client for internal operations
        external_client: MinIO client for generating presigned URLs
        bucket: Bucket name containing bundles

    Returns:
        List of BundleMetadata for each bundle
    """
    bundles = []
    prefix = f"{BUNDLE_PREFIX}/"

    try:
        objects = client.list_objects(bucket, prefix=prefix, recursive=True)

        for obj in objects:
            if obj.is_dir:
                continue

            # Parse object path: bundles/{publisher}/{book}/{filename}.zip
            parts = obj.object_name.split("/")
            if len(parts) < 4:
                continue

            file_name = parts[-1]
            if not file_name.endswith(".zip"):
                continue

            publisher_name = parts[1]
            book_name = parts[2]

            # Extract platform from filename (e.g., "(linux) FlowBook v1.4.11 - BookName.zip")
            platform = "unknown"
            lower_name = file_name.lower()
            for p in ALLOWED_PLATFORMS:
                if f"({p})" in lower_name:
                    platform = p
                    break

            # Generate presigned URL
            try:
                download_url = external_client.presigned_get_object(
                    bucket_name=bucket,
                    object_name=obj.object_name,
                    expires=timedelta(seconds=PRESIGNED_URL_EXPIRY_SECONDS),
                )
            except S3Error:
                download_url = None

            bundles.append(
                BundleMetadata(
                    publisher_name=publisher_name,
                    book_name=book_name,
                    platform=platform,
                    file_name=file_name,
                    file_size=obj.size,
                    created_at=obj.last_modified,
                    object_name=obj.object_name,
                    download_url=download_url,
                )
            )

    except S3Error as exc:
        logger.warning("Failed to list bundles: %s", exc)

    return sorted(bundles, key=lambda b: (b.publisher_name, b.book_name, b.platform))


class BundleNotFoundError(Exception):
    """Raised when a requested bundle does not exist."""

    pass


def delete_bundle(
    client: Minio,
    bucket: str,
    object_name: str,
) -> bool:
    """Delete a bundle.

    Args:
        client: MinIO client
        bucket: Bucket name
        object_name: Full object path of the bundle

    Returns:
        True if deleted successfully

    Raises:
        BundleNotFoundError: If bundle does not exist
    """
    # Check if exists first
    try:
        client.stat_object(bucket, object_name)
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            raise BundleNotFoundError(f"Bundle not found: {object_name}") from exc
        raise

    client.remove_object(bucket, object_name)
    logger.info("Deleted bundle: %s", object_name)
    return True
