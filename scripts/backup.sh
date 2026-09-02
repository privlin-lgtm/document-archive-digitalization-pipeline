#!/bin/sh
# Periodic pg_dump + document-volume archive. Restore:
#   gunzip -c backups/db-YYYYMMDDTHHMMSS.sql.gz | psql "$DATABASE_URL"
#   tar -xzf backups/documents-YYYYMMDDTHHMMSS.tar.gz -C /data/documents
set -eu

INTERVAL="${BACKUP_INTERVAL_SECONDS:-86400}"
DEST="${BACKUP_DIR:-/backups}"
mkdir -p "$DEST"

while true; do
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  echo "starting backup $stamp"
  if pg_dump --no-owner --no-acl | gzip > "$DEST/db-$stamp.sql.gz"; then
    echo "wrote $DEST/db-$stamp.sql.gz"
  else
    echo "pg_dump failed" >&2
    rm -f "$DEST/db-$stamp.sql.gz"
  fi
  if [ -d /data/documents ]; then
    tar -czf "$DEST/documents-$stamp.tar.gz" -C /data/documents . || echo "document archive failed" >&2
  fi
  # Keep the last 14 backups of each kind.
  ls -1t "$DEST"/db-*.sql.gz 2>/dev/null | tail -n +15 | xargs -r rm -f
  ls -1t "$DEST"/documents-*.tar.gz 2>/dev/null | tail -n +15 | xargs -r rm -f
  sleep "$INTERVAL"
done
