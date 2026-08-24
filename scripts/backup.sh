#!/usr/bin/env bash
# INT-11 · nightly backup of **both** stores. Owner: WANGLI (R-1).
#
#   scripts/backup.sh                     # PostgreSQL + MinIO, using the env below
#   scripts/backup.sh --pg-only           # skip the object store (not a full backup)
#
# Self-hosting bought architectural simplicity; backups are the price. Tickets
# still have Jira as a fallback in S1 — **logs and attachments have none from day
# one**, which is why this covers PostgreSQL *and* MinIO and why the restore
# drill (scripts/restore_drill.sh) restores both together. Restoring only the
# database yields intact prose with every image broken, and a half-restore that
# never shows up in a drill shows up during a real incident instead.
#
# Suggested values, unless the owner decides otherwise (INT-11):
#   * PostgreSQL: daily full + WAL archiving, 30 days of fulls / 7 days of WAL
#   * MinIO:      daily incremental to a separate host, 30 days
#   * drill:      once before the team starts writing real logs, quarterly after
#
# ⚠️ Two things this script deliberately does NOT do, because they need a human
# decision rather than a default:
#   * it does not configure WAL archiving. That is a postgresql.conf change
#     (archive_mode / archive_command) on the database host, and putting it in a
#     backup script would mean the archive silently depends on this cron entry
#     running. See the note at the end.
#   * it does not delete anything on the remote side. Retention is enforced
#     locally; a script that prunes a remote it just wrote to is one bad
#     variable away from being the incident.
#
# Exit status is non-zero if any component failed, so cron mails you.

set -euo pipefail

# ---------------------------------------------------------------- configuration

: "${RELAY_BACKUP_DIR:=/var/backups/relay}"
: "${RELAY_BACKUP_RETENTION_DAYS:=30}"

# PostgreSQL. Uses the standard libpq variables so this works with .pgpass, a
# service file, or a socket — whatever the deployment already has.
: "${PGHOST:=127.0.0.1}"
: "${PGPORT:=5432}"
: "${PGUSER:=relay_owner}"
: "${RELAY_PG_DATABASE:=relay}"

# MinIO. The mirror target is a **separate host** — a copy on the same disk is
# not a backup, it is a second way to lose the same disk.
: "${RELAY_MINIO_ALIAS:=relay-minio}"
: "${RELAY_MINIO_BUCKET:=relay-attachments}"
: "${RELAY_BACKUP_MINIO_MIRROR:=}"   # e.g. backup-host/relay-attachments-backup

PG_ONLY=0
[[ "${1:-}" == "--pg-only" ]] && PG_ONLY=1

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="${RELAY_BACKUP_DIR}/${STAMP}"
FAILED=0

log() { printf '[backup %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
fail() { FAILED=1; printf '[backup %s] FAILED: %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }

mkdir -p "$DEST"
log "writing to ${DEST}"

# ------------------------------------------------------------------ PostgreSQL

# Custom format (-Fc), not plain SQL: it restores selectively, in parallel, and
# pg_restore can list its contents — which is what makes a drill inspectable
# rather than a single 40-minute psql run you cannot interrupt.
if pg_dump --format=custom --compress=9 --file="${DEST}/postgres.dump" "${RELAY_PG_DATABASE}"; then
  # Verified immediately, because an unreadable dump discovered during an
  # incident is the same as no dump. This reads the archive's table of contents;
  # it does not prove the data restores (that is the drill's job).
  if pg_restore --list "${DEST}/postgres.dump" > "${DEST}/postgres.toc"; then
    log "postgres: $(du -h "${DEST}/postgres.dump" | cut -f1), TOC readable"
  else
    fail "postgres dump is not readable by pg_restore"
  fi
else
  fail "pg_dump"
fi

# The globals — roles and their passwords — are NOT in a per-database dump. A
# restore without them produces a database whose RLS policies name roles that do
# not exist, and the application cannot connect at all.
if pg_dumpall --globals-only --file="${DEST}/globals.sql"; then
  log "postgres: globals captured (roles, without them RLS policies name nothing)"
else
  fail "pg_dumpall --globals-only"
fi

# ----------------------------------------------------------------------- MinIO

if [[ "$PG_ONLY" -eq 1 ]]; then
  log "SKIPPING MinIO (--pg-only). This is not a complete backup: restoring it "
  log "would give you every log with every image broken."
elif [[ -z "$RELAY_BACKUP_MINIO_MIRROR" ]]; then
  fail "RELAY_BACKUP_MINIO_MIRROR is unset, so attachments were NOT backed up"
elif ! command -v mc > /dev/null; then
  fail "the MinIO client 'mc' is not installed, so attachments were NOT backed up"
else
  # Incremental by design (mirror copies only what changed) and NOT --remove:
  # mirroring a deletion is how a mistaken delete propagates into the backup
  # before anybody notices it happened.
  if mc mirror --quiet "${RELAY_MINIO_ALIAS}/${RELAY_MINIO_BUCKET}" "${RELAY_BACKUP_MINIO_MIRROR}"; then
    OBJECTS="$(mc ls --recursive "${RELAY_BACKUP_MINIO_MIRROR}" | wc -l)"
    printf '%s\n' "$OBJECTS" > "${DEST}/minio-object-count"
    log "minio: mirrored, ${OBJECTS} objects at the target"
    # An empty object store is normal on day one and alarming later. Saying so is
    # cheaper than discovering it in the drill.
    [[ "$OBJECTS" -eq 0 ]] && log "minio: NOTE the bucket is empty"
  else
    fail "mc mirror"
  fi
fi

# ------------------------------------------------------------------- retention

# Local pruning only, and only inside our own directory.
find "${RELAY_BACKUP_DIR}" -mindepth 1 -maxdepth 1 -type d \
  -mtime "+${RELAY_BACKUP_RETENTION_DAYS}" -print -exec rm -rf {} + || true

# ---------------------------------------------------------------------- report

cat > "${DEST}/MANIFEST" <<MANIFEST
relay backup ${STAMP}
database:        ${RELAY_PG_DATABASE} on ${PGHOST}:${PGPORT}
minio bucket:    ${RELAY_MINIO_ALIAS}/${RELAY_MINIO_BUCKET}
minio mirror:    ${RELAY_BACKUP_MINIO_MIRROR:-<not configured>}
pg-only:         ${PG_ONLY}
retention:       ${RELAY_BACKUP_RETENTION_DAYS} days (local)
restore:         scripts/restore_drill.sh ${DEST}
MANIFEST

if [[ "$FAILED" -ne 0 ]]; then
  log "one or more components failed — see above"
  exit 1
fi
log "complete"

# ⚠️ WAL archiving is not this script's job and is not optional. A daily full
# dump means up to 24 hours of accepted loss; the drill's stated objective is
# "restore both together and open a log that contains an image", and a
# point-in-time recovery needs the WAL. Set on the database host:
#
#   archive_mode = on
#   archive_command = 'test ! -f /var/backups/relay/wal/%f && cp %p /var/backups/relay/wal/%f'
#
# and prune that directory at 7 days. Do it in postgresql.conf, not here, so the
# archive does not depend on a cron entry that somebody can disable.
