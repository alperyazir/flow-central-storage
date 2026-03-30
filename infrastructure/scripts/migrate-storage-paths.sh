#!/usr/bin/env bash
# Migrate S3 storage paths from publisher name to publisher ID
# Usage: ./migrate-storage-paths.sh
#
# This script reads publisher name→ID mapping from the API database,
# then renames all S3 objects from {name}/... to {id}/...

set -euo pipefail

S3_ENDPOINT="${S3_ENDPOINT:-http://localhost:8333}"
S3_ACCESS_KEY="${S3_ACCESS_KEY:-admin}"
S3_SECRET_KEY="${S3_SECRET_KEY:-admin}"
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5433}"
DB_NAME="${DB_NAME:-flow_central}"
DB_USER="${DB_USER:-postgres}"
DB_PASSWORD="${DB_PASSWORD:-changethis}"

RCLONE_CONF="/tmp/rclone-migrate-paths.conf"

echo "=== Storage Path Migration: Publisher Name → ID ==="
echo "S3 Endpoint: $S3_ENDPOINT"
echo ""

# Check dependencies
command -v rclone &>/dev/null || { echo "ERROR: rclone not installed"; exit 1; }
command -v psql &>/dev/null || { echo "ERROR: psql not installed"; exit 1; }

# Create rclone config
cat > "$RCLONE_CONF" << EOF
[s3]
type = s3
provider = Other
endpoint = $S3_ENDPOINT
access_key_id = $S3_ACCESS_KEY
secret_access_key = $S3_SECRET_KEY
EOF

# Get publisher name→ID mapping from database
echo "Fetching publisher mapping from database..."
PUBLISHERS=$(PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t -A -F'|' -c "SELECT id, name FROM publishers WHERE status != 'archived' ORDER BY id")

if [ -z "$PUBLISHERS" ]; then
    echo "No publishers found in database."
    exit 0
fi

echo "Publishers found:"
echo "$PUBLISHERS" | while IFS='|' read -r id name; do
    echo "  ID=$id  Name=$name"
done
echo ""

# Migrate each publisher's objects in the publishers bucket
echo "=== Migrating 'publishers' bucket ==="
echo "$PUBLISHERS" | while IFS='|' read -r id name; do
    # Check if old name-based prefix has objects
    count=$(rclone --config "$RCLONE_CONF" size "s3:publishers/$name/" --json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('count',0))" 2>/dev/null || echo "0")

    if [ "$count" = "0" ]; then
        echo "  SKIP: '$name' has no objects (already migrated or empty)"
        continue
    fi

    echo "  Migrating '$name' → '$id' ($count objects)..."
    rclone --config "$RCLONE_CONF" copy "s3:publishers/$name/" "s3:publishers/$id/" --progress 2>&1 | tail -1

    # Verify
    new_count=$(rclone --config "$RCLONE_CONF" size "s3:publishers/$id/" --json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('count',0))" 2>/dev/null || echo "0")

    if [ "$count" = "$new_count" ]; then
        echo "  OK: Verified $new_count objects at '$id/'"
        echo "  Removing old prefix '$name/'..."
        rclone --config "$RCLONE_CONF" purge "s3:publishers/$name/" 2>/dev/null || true
        echo "  Done."
    else
        echo "  WARNING: Count mismatch! Source=$count Dest=$new_count — old prefix NOT deleted"
    fi
done

echo ""
echo "=== Migration Complete ==="
echo ""
echo "Restart API to use new paths: docker compose restart api worker"

# Cleanup
rm -f "$RCLONE_CONF"
