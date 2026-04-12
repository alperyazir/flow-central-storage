"""Clean up incomplete multipart uploads from R2/S3 buckets.

Usage:
    python scripts/cleanup_r2_incomplete.py          # list only (dry-run)
    python scripts/cleanup_r2_incomplete.py --abort   # abort all incomplete uploads

Reads credentials from apps/api/.env automatically.
"""

import argparse
import os
import sys
from pathlib import Path

# Load .env from api directory
env_path = Path(__file__).resolve().parent.parent / "apps" / "api" / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

from minio import Minio


def get_client() -> Minio:
    raw_endpoint = os.environ.get(
        "FCS_MINIO_ENDPOINT", "https://db3412739865b5b6a130a8b5a9dbfa66.r2.cloudflarestorage.com")
    # Strip protocol prefix — Minio client handles https via secure flag
    endpoint = raw_endpoint.replace("https://", "").replace("http://", "").rstrip("/")
    access_key = os.environ.get("FCS_MINIO_ACCESS_KEY", "ecdc42686d10f18b835639fe66a82797")
    secret_key = os.environ.get("FCS_MINIO_SECRET_KEY", "0f268faa229cb7e3f37f8a713310cf53db3c2019a1ba71518b5e488de4c437a5")
    secure = os.environ.get("FCS_MINIO_SECURE", "true").lower() == "true"
    print(f"Connecting to {endpoint} (secure={secure})")
    return Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)


def main():
    parser = argparse.ArgumentParser(description="Clean up incomplete multipart uploads")
    parser.add_argument("--abort", action="store_true", help="Abort all incomplete uploads (default: list only)")
    parser.add_argument("--buckets", nargs="+", help="Bucket names to check (e.g. --buckets my-publishers my-apps)")
    args = parser.parse_args()

    client = get_client()

    if args.buckets:
        buckets = args.buckets
        print(f"Using specified buckets: {buckets}")
    else:
        # Auto-discover buckets
        try:
            all_buckets = client.list_buckets()
            buckets = [b.name for b in all_buckets]
            print(f"Found buckets: {buckets}")
        except Exception as e:
            print(f"Could not list buckets ({e}), using defaults")
            buckets = ["flow-publishers", "flow-apps", "flow-trash", "flow-teachers"]

    total = 0
    aborted = 0

    for bucket in buckets:
        try:
            result = client._list_multipart_uploads(bucket)
            uploads = list(result.uploads) if result.uploads else []
            if not uploads:
                print(f"  {bucket}: no incomplete uploads")
                continue

            print(f"\n  {bucket}: {len(uploads)} incomplete upload(s)")
            for upload in uploads:
                total += 1
                initiated = upload.initiated_time.isoformat() if upload.initiated_time else "unknown"
                print(f"    - {upload.object_name}  (id: {upload.upload_id}, initiated: {initiated})")

                if args.abort:
                    try:
                        client._abort_multipart_upload(bucket, upload.object_name, upload.upload_id)
                        aborted += 1
                        print(f"      -> ABORTED")
                    except Exception as e:
                        print(f"      -> ERROR: {e}")
        except Exception as e:
            print(f"  {bucket}: error listing - {e}")

    print(f"\nTotal: {total} incomplete upload(s)")
    if args.abort:
        print(f"Aborted: {aborted}")
    elif total > 0:
        print("Run with --abort to clean them up")


if __name__ == "__main__":
    main()
