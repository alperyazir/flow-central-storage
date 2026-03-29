#!/usr/bin/env bash
# Migrate data from MinIO to SeaweedFS using rclone
# Prerequisites: rclone installed, both MinIO and SeaweedFS running
# Usage: ./migrate-minio-to-seaweedfs.sh

set -euo pipefail

MINIO_ENDPOINT="${MINIO_ENDPOINT:-http://localhost:9000}"
MINIO_ACCESS_KEY="${MINIO_ACCESS_KEY:-flow_minio}"
MINIO_SECRET_KEY="${MINIO_SECRET_KEY:-flow_minio_secret}"

SEAWEEDFS_ENDPOINT="${SEAWEEDFS_ENDPOINT:-http://localhost:8333}"
SEAWEEDFS_ACCESS_KEY="${SEAWEEDFS_ACCESS_KEY:-admin}"
SEAWEEDFS_SECRET_KEY="${SEAWEEDFS_SECRET_KEY:-admin}"

BUCKETS="publishers apps teachers trash"

echo "=== MinIO → SeaweedFS Migration ==="
echo "Source:      $MINIO_ENDPOINT"
echo "Destination: $SEAWEEDFS_ENDPOINT"
echo ""

# Check rclone is installed
if ! command -v rclone &> /dev/null; then
    echo "ERROR: rclone not installed. Install with: brew install rclone (macOS) or apt install rclone (Linux)"
    exit 1
fi

# Configure remotes (in-memory, no persistent config)
export RCLONE_CONFIG_MINIO_TYPE=s3
export RCLONE_CONFIG_MINIO_PROVIDER=Minio
export RCLONE_CONFIG_MINIO_ENDPOINT="$MINIO_ENDPOINT"
export RCLONE_CONFIG_MINIO_ACCESS_KEY_ID="$MINIO_ACCESS_KEY"
export RCLONE_CONFIG_MINIO_SECRET_ACCESS_KEY="$MINIO_SECRET_KEY"

export RCLONE_CONFIG_SEAWEEDFS_TYPE=s3
export RCLONE_CONFIG_SEAWEEDFS_PROVIDER=Other
export RCLONE_CONFIG_SEAWEEDFS_ENDPOINT="$SEAWEEDFS_ENDPOINT"
export RCLONE_CONFIG_SEAWEEDFS_ACCESS_KEY_ID="$SEAWEEDFS_ACCESS_KEY"
export RCLONE_CONFIG_SEAWEEDFS_SECRET_ACCESS_KEY="$SEAWEEDFS_SECRET_KEY"

# Migrate each bucket
for bucket in $BUCKETS; do
    echo "--- Migrating bucket: $bucket ---"

    # Check if source bucket exists
    if ! rclone lsd minio:"$bucket" &> /dev/null; then
        echo "  SKIP: Source bucket '$bucket' does not exist in MinIO"
        continue
    fi

    # Count source objects
    src_count=$(rclone size minio:"$bucket" --json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('count',0))" 2>/dev/null || echo "0")
    echo "  Source objects: $src_count"

    # Ensure destination bucket exists
    rclone mkdir seaweedfs:"$bucket" 2>/dev/null || true

    # Sync (idempotent — skips existing identical files)
    rclone sync minio:"$bucket" seaweedfs:"$bucket" --progress --transfers=4

    # Count destination objects
    dst_count=$(rclone size seaweedfs:"$bucket" --json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('count',0))" 2>/dev/null || echo "0")
    echo "  Destination objects: $dst_count"

    if [ "$src_count" = "$dst_count" ]; then
        echo "  OK: Counts match ($src_count)"
    else
        echo "  WARNING: Count mismatch! Source=$src_count Destination=$dst_count"
    fi
    echo ""
done

echo "=== Migration Complete ==="
echo ""
echo "Next steps:"
echo "  1. Verify data in SeaweedFS filer UI: http://localhost:8888"
echo "  2. Update .env to point to SeaweedFS endpoint"
echo "  3. Restart API services"
echo "  4. Keep MinIO data until fully verified"
echo ""
echo "Rollback: Point FCS_MINIO_ENDPOINT back to MinIO (localhost:9000)"
