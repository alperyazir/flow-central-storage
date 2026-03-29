#!/bin/bash
# DCS PostgreSQL backup script
# Usage: ./scripts/backup-db.sh
# Cron: 0 2 * * * /opt/flow-central-storage/infrastructure/scripts/backup-db.sh

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/backups/dcs-postgres}"
RETENTION_DAILY=7
RETENTION_WEEKLY=4
DB_CONTAINER="${DB_CONTAINER:-infrastructure-postgres-1}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_DB="${POSTGRES_DB:-flow_central}"
DATE=$(date +%Y%m%d_%H%M%S)
DAY_OF_WEEK=$(date +%u)

mkdir -p "$BACKUP_DIR/daily" "$BACKUP_DIR/weekly"

echo "[$(date)] Starting backup of $POSTGRES_DB..."

BACKUP_FILE="$BACKUP_DIR/daily/${POSTGRES_DB}_${DATE}.sql.gz"
docker exec "$DB_CONTAINER" pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > "$BACKUP_FILE"

BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "[$(date)] Backup created: $BACKUP_FILE ($BACKUP_SIZE)"

if [ "$DAY_OF_WEEK" -eq 7 ]; then
    cp "$BACKUP_FILE" "$BACKUP_DIR/weekly/${POSTGRES_DB}_weekly_${DATE}.sql.gz"
    echo "[$(date)] Weekly backup created"
fi

find "$BACKUP_DIR/daily" -name "*.sql.gz" -mtime +$RETENTION_DAILY -delete
find "$BACKUP_DIR/weekly" -name "*.sql.gz" -mtime +$((RETENTION_WEEKLY * 7)) -delete

echo "[$(date)] Backup complete"
