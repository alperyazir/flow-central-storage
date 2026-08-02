#!/usr/bin/env bash
#
# Back up every book's AI-generated data (the `ai-data/` trees) from R2 as a
# single dated archive.
#
#   scripts/backup_ai_data.sh [destination-dir]
#
# Only ai-data is copied. The source material (PDFs, images, book audio) is
# ~106 GB and already lives on the SSD under workspace/prod; ai-data is under
# 1 GB and is the only part that cannot be recovered without re-running the
# whole LLM + TTS pipeline.
#
# Why an archive rather than loose files: ai-data is ~100k objects averaging
# 10 KB, and the external SSD is exFAT with 1 MB allocation units — every file
# there consumes a full megabyte. Stored loose, 970 MB of data would occupy
# ~100 GB. A single archive sidesteps that entirely.
#
# So this runs in two stages:
#   1. rclone mirrors R2 into a staging directory on the internal disk (APFS,
#      4 KB blocks). Re-runs only fetch what changed, so this is cheap to
#      repeat.
#   2. the staging directory is packed into a dated .tar.zst on the destination.
#
# Nothing is ever deleted: `rclone copy` (not `sync`) means a book removed from
# R2 survives in the staging dir and in every archive already written.
#
# Credentials come from apps/api/.env — never hardcode them here.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/apps/api/.env"
DEST="${1:-/Volumes/Extreme SSD/r2-backup}"
STAGING="${FCS_BACKUP_STAGING:-$HOME/.cache/flow-central-storage/ai-data}"

for tool in rclone zstd; do
  command -v "$tool" >/dev/null 2>&1 || { echo "$tool is required: brew install $tool" >&2; exit 1; }
done

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE — cannot read R2 credentials" >&2
  exit 1
fi

# shellcheck disable=SC1090
set -a && . "$ENV_FILE" && set +a

: "${FCS_MINIO_ENDPOINT:?not set in apps/api/.env}"
: "${FCS_MINIO_ACCESS_KEY:?not set in apps/api/.env}"
: "${FCS_MINIO_SECRET_KEY:?not set in apps/api/.env}"
BUCKET="${FCS_MINIO_PUBLISHERS_BUCKET:-flow-publishers}"

# Define the remote through the environment so no credentials are ever written
# to an rclone config file.
export RCLONE_CONFIG_R2_TYPE=s3
export RCLONE_CONFIG_R2_PROVIDER=Cloudflare
export RCLONE_CONFIG_R2_ENV_AUTH=false
export RCLONE_CONFIG_R2_ACCESS_KEY_ID="$FCS_MINIO_ACCESS_KEY"
export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$FCS_MINIO_SECRET_KEY"
export RCLONE_CONFIG_R2_REGION="${FCS_MINIO_REGION:-auto}"
export RCLONE_CONFIG_R2_ENDPOINT="https://${FCS_MINIO_ENDPOINT#https://}"
export RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true

ARCHIVE="$DEST/ai-data-$(date +%Y%m%d-%H%M).tar.zst"
mkdir -p "$STAGING" "$DEST"

echo "1/3  R2:$BUCKET (ai-data)  ->  $STAGING"
# --transfers/--checkers are high because the bottleneck with ~100k small
# objects is request latency, not bandwidth.
rclone copy "R2:$BUCKET" "$STAGING" \
  --include "**/ai-data/**" \
  --transfers 32 \
  --checkers 32 \
  --fast-list \
  --retries 3 \
  --low-level-retries 10 \
  --stats 15s \
  --stats-one-line \
  --progress

STAGED=$(find "$STAGING" -type f | wc -l | tr -d ' ')
echo
echo "2/3  packing $STAGED files  ->  $ARCHIVE"
tar -C "$STAGING" -cf - . | zstd -6 -T0 -o "$ARCHIVE" -q --force

echo
echo "3/3  verifying"
zstd -t "$ARCHIVE"
PACKED=$(zstd -dc "$ARCHIVE" | tar -tf - | grep -cv '/$' || true)
echo -n "remote: "
rclone size "R2:$BUCKET" --include "**/ai-data/**" --fast-list

echo "staged  : $STAGED files"
echo "archived: $PACKED files"
echo "archive : $(du -h "$ARCHIVE" | cut -f1)  $ARCHIVE"

if [[ "$STAGED" != "$PACKED" ]]; then
  echo "MISMATCH: staged and archived file counts differ" >&2
  exit 1
fi
echo "OK — compare 'staged' against the remote object count above."
