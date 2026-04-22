"""Migrate child-book R2 objects from flat layout to nested-under-parent.

Flat layout (v2):
    {publisher_slug}/books/{child_book_name}/...

Nested layout (v2.1):
    {publisher_slug}/books/{parent_book_name}/additional-resources/{child_book_name}/...

Reads child↔parent relationships from the database (books table with
``parent_book_id``) and uses Minio server-side copy to move objects. Safe
to re-run; actions are idempotent per-child.

Usage:
    python scripts/migrate-children-to-nested.py                     # dry-run
    python scripts/migrate-children-to-nested.py --apply             # copy + delete old
    python scripts/migrate-children-to-nested.py --apply --keep-old  # copy only, keep flat as backup
    python scripts/migrate-children-to-nested.py --apply --rollback  # reverse: move nested -> flat

Environment:
    Reads FCS_* vars from apps/api/.env (same pattern as cleanup_r2_incomplete.py).
    Requires network access to R2/MinIO and the Postgres DB.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

# Load .env from api directory (same pattern as scripts/cleanup_r2_incomplete.py:16-23)
env_path = Path(__file__).resolve().parent.parent / "apps" / "api" / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

import psycopg
from minio import Minio
from minio.commonconfig import CopySource
from minio.error import S3Error


def get_minio_client() -> Minio:
    raw_endpoint = os.environ.get("FCS_MINIO_ENDPOINT", "")
    if not raw_endpoint:
        print("ERROR: FCS_MINIO_ENDPOINT not set", file=sys.stderr)
        sys.exit(1)
    endpoint = raw_endpoint.replace("https://", "").replace("http://", "").rstrip("/")
    access_key = os.environ.get("FCS_MINIO_ACCESS_KEY", "")
    secret_key = os.environ.get("FCS_MINIO_SECRET_KEY", "")
    secure = os.environ.get("FCS_MINIO_SECURE", "true").lower() == "true"
    if not access_key or not secret_key:
        print("ERROR: FCS_MINIO_ACCESS_KEY / FCS_MINIO_SECRET_KEY not set", file=sys.stderr)
        sys.exit(1)
    return Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)


def get_publishers_bucket() -> str:
    return os.environ.get("FCS_MINIO_PUBLISHERS_BUCKET", "flow-publishers")


def connect_db() -> psycopg.Connection:
    # Matches the API container env scheme (FCS_DATABASE_*).
    url = os.environ.get("FCS_DATABASE_URL")
    if not url:
        user = os.environ.get("FCS_DATABASE_USER", "postgres")
        password = os.environ.get("FCS_DATABASE_PASSWORD", "postgres")
        host = os.environ.get("FCS_DATABASE_HOST", "localhost")
        port = os.environ.get("FCS_DATABASE_PORT", "5433")
        db = os.environ.get("FCS_DATABASE_NAME", "flow_central")
        url = f"postgresql://{user}:{password}@{host}:{port}/{db}"
    return psycopg.connect(url)


@dataclass
class ChildMapping:
    child_id: int
    child_name: str
    parent_name: str
    publisher_slug: str

    def flat_prefix(self) -> str:
        return f"{self.publisher_slug}/books/{self.child_name}/"

    def nested_prefix(self) -> str:
        return f"{self.publisher_slug}/books/{self.parent_name}/additional-resources/{self.child_name}/"


def fetch_children(conn: psycopg.Connection) -> list[ChildMapping]:
    """Return all child books from DB with their parent's book_name and publisher slug."""
    sql = """
        SELECT b.id, b.book_name, p.book_name AS parent_name, pub.slug
        FROM books b
        JOIN books p ON b.parent_book_id = p.id
        JOIN publishers pub ON b.publisher_id = pub.id
        WHERE b.parent_book_id IS NOT NULL
        ORDER BY pub.slug, p.book_name, b.book_name;
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        return [
            ChildMapping(child_id=row[0], child_name=row[1], parent_name=row[2], publisher_slug=row[3])
            for row in cur.fetchall()
        ]


def list_object_keys(client: Minio, bucket: str, prefix: str) -> list[str]:
    try:
        return [
            obj.object_name
            for obj in client.list_objects(bucket, prefix=prefix, recursive=True)
            if not obj.is_dir
        ]
    except S3Error:
        return []


def copy_prefix(
    client: Minio,
    bucket: str,
    src_prefix: str,
    dst_prefix: str,
    dry_run: bool,
) -> tuple[int, list[str]]:
    """Copy every object under src_prefix to dst_prefix. Returns (copied_count, copied_keys)."""
    src_keys = list_object_keys(client, bucket, src_prefix)
    copied: list[str] = []
    for src_key in src_keys:
        rel = src_key[len(src_prefix):]
        dst_key = dst_prefix + rel
        if dry_run:
            copied.append(dst_key)
            continue
        try:
            client.copy_object(
                bucket_name=bucket,
                object_name=dst_key,
                source=CopySource(bucket, src_key),
            )
            copied.append(dst_key)
        except S3Error as exc:
            print(f"  ! COPY FAILED: {src_key} -> {dst_key}: {exc}", file=sys.stderr)
    return len(copied), copied


def delete_prefix(client: Minio, bucket: str, prefix: str, dry_run: bool) -> int:
    keys = list_object_keys(client, bucket, prefix)
    if dry_run:
        return len(keys)
    deleted = 0
    for key in keys:
        try:
            client.remove_object(bucket, key)
            deleted += 1
        except S3Error as exc:
            print(f"  ! DELETE FAILED: {key}: {exc}", file=sys.stderr)
    return deleted


def migrate(client: Minio, bucket: str, child: ChildMapping, args: argparse.Namespace) -> str:
    """Move one child's R2 content to nested layout. Return status string."""
    if args.rollback:
        src_prefix = child.nested_prefix()
        dst_prefix = child.flat_prefix()
    else:
        src_prefix = child.flat_prefix()
        dst_prefix = child.nested_prefix()

    src_keys = list_object_keys(client, bucket, src_prefix)
    dst_keys = list_object_keys(client, bucket, dst_prefix)

    # Idempotency: if destination already has matching content and source is empty, done.
    if not src_keys and dst_keys:
        return "already migrated (source empty, dest has objects)"
    if not src_keys and not dst_keys:
        return "skipped — nothing at source OR destination"

    # If both exist, we'd be copying over — usually fine with S3 copy_object (overwrite).
    # Report count for visibility.
    src_count = len(src_keys)
    dst_count = len(dst_keys)

    print(f"    src: {src_prefix} ({src_count} objects)")
    print(f"    dst: {dst_prefix} (currently {dst_count} objects)")

    copied_count, _ = copy_prefix(client, bucket, src_prefix, dst_prefix, dry_run=not args.apply)
    if copied_count != src_count:
        return f"PARTIAL: copied {copied_count}/{src_count}"

    if args.keep_old:
        return f"copied {copied_count} objects (kept old prefix)"

    deleted = delete_prefix(client, bucket, src_prefix, dry_run=not args.apply)
    return f"copied {copied_count}, deleted {deleted} from old prefix"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Actually perform copies/deletes (default: dry-run)")
    parser.add_argument("--keep-old", action="store_true", help="Copy only; don't delete old flat prefix")
    parser.add_argument("--rollback", action="store_true", help="Reverse direction: nested -> flat")
    args = parser.parse_args()

    if args.rollback and args.keep_old:
        print("ERROR: --rollback and --keep-old are not compatible", file=sys.stderr)
        sys.exit(1)

    mode = []
    mode.append("APPLY" if args.apply else "DRY-RUN")
    if args.rollback:
        mode.append("ROLLBACK")
    if args.keep_old:
        mode.append("KEEP-OLD")
    print(f"=== Migrate child books — mode: {' + '.join(mode)} ===\n")

    client = get_minio_client()
    bucket = get_publishers_bucket()
    print(f"R2 bucket: {bucket}")

    conn = connect_db()
    try:
        children = fetch_children(conn)
    finally:
        conn.close()

    if not children:
        print("No child books found in DB. Nothing to migrate.")
        return

    print(f"Found {len(children)} child book(s):\n")
    results: list[tuple[ChildMapping, str]] = []
    for child in children:
        label = f"[{child.publisher_slug}] {child.parent_name} / {child.child_name} (id={child.child_id})"
        print(f"→ {label}")
        try:
            status = migrate(client, bucket, child, args)
        except Exception as exc:
            status = f"ERROR: {exc}"
        print(f"    result: {status}\n")
        results.append((child, status))

    # Summary
    print("\n=== Summary ===")
    for child, status in results:
        print(f"  {child.publisher_slug}/{child.parent_name}/{child.child_name}: {status}")
    if not args.apply:
        print("\n(Dry-run. Re-run with --apply to make changes.)")


if __name__ == "__main__":
    main()
