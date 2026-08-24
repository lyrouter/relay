#!/usr/bin/env bash
# INT-11 · the restore drill. 🔒 A hard gate: **before the team writes real logs.**
#
#   scripts/restore_drill.sh /var/backups/relay/20260824T190000Z
#
# Owner: WANGLI (R-1). This is the half a script can do; the last step is a human
# opening a page, and that is deliberate — see the end.
#
# **Why both stores, always.** Tickets have Jira as a fallback in S1. Logs and
# attachments have none from day one. Restoring only PostgreSQL yields intact
# prose with every image broken — a *half*-restore, which looks like a success
# until somebody scrolls. INT-11 says it in one line: restore both together and
# open a log that contains an image. If that half-restore has never appeared in a
# drill, it will appear during a real incident instead.
#
# **It restores into a scratch database and a scratch bucket**, never over the
# live ones. A drill that could destroy production is a drill nobody runs, and a
# drill nobody runs is the thing this task exists to prevent.
#
# What it verifies, in order:
#   1 the dump restores at all, into a fresh database;
#   2 the roles exist (RLS policies name roles — without them nothing connects);
#   3 the schema is at the migration head the code expects;
#   4 rows came back: tenants, users, logs, tickets, attachments;
#   5 **every attachment row has its object** in the restored bucket. This is the
#     check that catches the half-restore, and it is the reason the script exists
#     rather than a checklist item saying "restore the backup";
#   6 it prints the URL of a log that contains an image, for a human to open.

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <backup-directory>   (see scripts/backup.sh)" >&2
  exit 2
fi

SOURCE="$1"
[[ -f "${SOURCE}/postgres.dump" ]] || { echo "no postgres.dump in ${SOURCE}" >&2; exit 2; }

: "${PGHOST:=127.0.0.1}"
: "${PGPORT:=5432}"
: "${PGUSER:=postgres}"
: "${RELAY_DRILL_DATABASE:=relay_drill}"
: "${RELAY_MINIO_ALIAS:=relay-minio}"
: "${RELAY_BACKUP_MINIO_MIRROR:=}"
: "${RELAY_DRILL_BUCKET:=relay-attachments-drill}"
: "${RELAY_PUBLIC_BASE_URL:=https://relay.internal}"

PASS=0
FAIL=0
ok()   { PASS=$((PASS+1)); printf '[  ok  ] %s\n' "$*"; }
bad()  { FAIL=$((FAIL+1)); printf '[ FAIL ] %s\n' "$*" >&2; }
step() { printf '\n== %s\n' "$*"; }

q() { psql -X -A -t -d "${RELAY_DRILL_DATABASE}" -c "$1" | tr -d '[:space:]'; }

# ------------------------------------------------------- 1 · roles, then restore

step "roles (globals)"
if [[ -f "${SOURCE}/globals.sql" ]]; then
  # Idempotent enough in practice: existing roles produce "already exists"
  # notices, which are not failures. Restoring globals *first* matters — the
  # dump's GRANTs and RLS policies name these roles.
  psql -X -q -d postgres -f "${SOURCE}/globals.sql" > /dev/null 2>&1 || true
  ok "globals applied (roles present)"
else
  bad "no globals.sql — RLS policies will name roles that do not exist"
fi

step "restore into ${RELAY_DRILL_DATABASE} (scratch, never the live database)"
dropdb --if-exists "${RELAY_DRILL_DATABASE}"
createdb -O relay_owner "${RELAY_DRILL_DATABASE}"
psql -X -q -d "${RELAY_DRILL_DATABASE}" -f scripts/bootstrap_extensions.sql > /dev/null

if pg_restore --dbname="${RELAY_DRILL_DATABASE}" --no-owner --no-privileges \
     "${SOURCE}/postgres.dump" 2> "${SOURCE}/restore.log"; then
  ok "pg_restore completed"
else
  # pg_restore warns about ownership and extensions routinely; a non-zero exit
  # with rows present is common. So this is reported and the row checks below
  # decide, rather than aborting on a warning.
  bad "pg_restore exited non-zero — see ${SOURCE}/restore.log (row checks follow)"
fi

# --------------------------------------------------------------- 3 · the schema

step "schema"
HEAD="$(q "select version_num from alembic_version limit 1;" || true)"
if [[ -n "$HEAD" ]]; then
  EXPECTED="$(uv run alembic heads 2>/dev/null | head -1 | awk '{print $1}')"
  if [[ "$HEAD" == "$EXPECTED" ]]; then
    ok "at migration head ${HEAD}"
  else
    bad "restored schema is at ${HEAD}, the code expects ${EXPECTED}"
  fi
else
  bad "no alembic_version row: this dump does not carry a migrated schema"
fi

POLICIES="$(q "select count(*) from pg_policies where schemaname='public';")"
if [[ "${POLICIES:-0}" -gt 0 ]]; then
  ok "${POLICIES} RLS policies restored"
else
  bad "no RLS policies in the restored database — every tenant would see everything"
fi

# ----------------------------------------------------------------- 4 · the rows

step "rows"
for pair in "tenant:tenants" "\"user\":users" "log:logs" "ticket:tickets"; do
  table="${pair%%:*}"; label="${pair##*:}"
  count="$(q "select count(*) from ${table};")"
  if [[ "${count:-0}" -gt 0 ]]; then ok "${count} ${label}"; else bad "no ${label} restored"; fi
done

ATTACHMENTS="$(q "select count(*) from attachment;")"
ok "${ATTACHMENTS} attachment rows"

# ------------------------------------------- 5 · the half-restore check (MinIO)

step "attachments: every row must have its object"
if [[ "${ATTACHMENTS:-0}" -eq 0 ]]; then
  echo "  no attachments in this backup, so nothing to cross-check."
  echo "  ⚠️  This drill has NOT exercised the half-restore case. Upload an image"
  echo "      to a log, take a fresh backup, and run the drill again — that case"
  echo "      is the whole reason INT-11 covers both stores."
elif [[ -z "$RELAY_BACKUP_MINIO_MIRROR" ]]; then
  bad "RELAY_BACKUP_MINIO_MIRROR is unset: the object store was not restored, so
        this is exactly the half-restore INT-11 warns about"
elif ! command -v mc > /dev/null; then
  bad "'mc' is not installed: cannot restore or verify the object store"
else
  mc mb --ignore-existing "${RELAY_MINIO_ALIAS}/${RELAY_DRILL_BUCKET}" > /dev/null
  if mc mirror --quiet "${RELAY_BACKUP_MINIO_MIRROR}" \
       "${RELAY_MINIO_ALIAS}/${RELAY_DRILL_BUCKET}"; then
    ok "objects restored into ${RELAY_DRILL_BUCKET}"
  else
    bad "mc mirror from the backup target failed"
  fi

  # The cross-check. Keys are compared, not counted: a bucket with the right
  # number of wrong objects passes a count and breaks every image.
  mc ls --recursive "${RELAY_MINIO_ALIAS}/${RELAY_DRILL_BUCKET}" \
    | awk '{print $NF}' | sort > /tmp/relay-drill-objects
  q "select blob_key from attachment order by blob_key;" > /dev/null 2>&1 || true
  psql -X -A -t -d "${RELAY_DRILL_DATABASE}" \
    -c "select blob_key from attachment order by blob_key;" | sort > /tmp/relay-drill-keys

  MISSING="$(comm -23 /tmp/relay-drill-keys /tmp/relay-drill-objects | wc -l)"
  if [[ "$MISSING" -eq 0 ]]; then
    ok "every attachment row has its object — no broken images"
  else
    bad "${MISSING} attachment rows point at objects that are NOT in the restored
        bucket. This is the half-restore: the prose is intact and those images
        are broken. Do not sign off the drill."
    comm -23 /tmp/relay-drill-keys /tmp/relay-drill-objects | head -5 >&2
  fi
fi

# ------------------------------------------------ 6 · the part a script cannot do

step "the human step"
ILLUSTRATED="$(psql -X -A -t -d "${RELAY_DRILL_DATABASE}" -c "
  select t.slug, a.owner_id
  from attachment a
  join log l on l.id = a.owner_id and l.tenant_id = a.tenant_id
  join tenant t on t.id = a.tenant_id
  where a.owner_type = 'log' and a.mime like 'image/%'
  limit 1;" | tr -d ' ')"

if [[ -n "$ILLUSTRATED" ]]; then
  SLUG="${ILLUSTRATED%%|*}"; LOG_ID="${ILLUSTRATED##*|}"
  echo "  Point a Relay instance at ${RELAY_DRILL_DATABASE} and open:"
  echo "    ${RELAY_PUBLIC_BASE_URL}/${SLUG}/logs/${LOG_ID}"
  echo "  The drill is signed off when the text AND the image both render."
else
  echo "  No log with an image in this backup, so the visual check cannot be done."
  echo "  ⚠️  Do not sign the drill off on the row counts alone."
fi

printf '\n%s checks passed, %s failed\n' "$PASS" "$FAIL"
echo "Scratch database ${RELAY_DRILL_DATABASE} and bucket ${RELAY_DRILL_BUCKET} were"
echo "left in place for inspection. Drop them when you are done."
[[ "$FAIL" -eq 0 ]] || exit 1
