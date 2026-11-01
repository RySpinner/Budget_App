#!/usr/bin/env bash
# Nightly backup (SPEC section 9).
#
#   1. Dump the database to BACKUP_DIR/daily/household_budget-YYYY-MM-DD.dump
#      (pg_dump custom format) and check the dump can be read back.
#   2. Keep one dump per month in BACKUP_DIR/monthly/.
#   3. Delete daily dumps older than KEEP_DAILY days; keep the newest
#      KEEP_MONTHLY monthly dumps.
#   4. Copy everything off the Pi with rsync over SSH:
#        REMOTE/db/          mirror of BACKUP_DIR (same retention)
#        REMOTE/csv-archive/ the original CSV exports (only ever added to)
#
# Usage: backup.sh /etc/household-budget/backup.env
# Exits non-zero if anything fails (including the off-Pi copy), so
# `systemctl status household-budget-backup` shows a failure.
set -euo pipefail

CONFIG="${1:-/etc/household-budget/backup.env}"
# shellcheck source=/dev/null
source "$CONFIG"
: "${DB_NAME:?}" "${DB_USER:?}" "${BACKUP_DIR:?}" "${ARCHIVE_DIR:?}"
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
KEEP_DAILY="${KEEP_DAILY:-14}"
KEEP_MONTHLY="${KEEP_MONTHLY:-12}"

log() { echo "[backup] $*"; }

mkdir -p "$BACKUP_DIR/daily" "$BACKUP_DIR/monthly"
today="$(date +%F)"            # e.g. 2026-11-01
month="$(date +%Y-%m)"         # e.g. 2026-11
dump="$BACKUP_DIR/daily/household_budget-$today.dump"

# 1. Dump to a temporary name first, so a half-written file never looks like a backup.
log "dumping $DB_NAME to $dump"
pg_dump --format=custom --host="$DB_HOST" --port="$DB_PORT" --username="$DB_USER" \
        --no-password --file="$dump.partial" "$DB_NAME"
pg_restore --list "$dump.partial" > /dev/null     # fails if the dump is unreadable
mv "$dump.partial" "$dump"
log "dump OK ($(du -h "$dump" | cut -f1))"

# 2. First dump of each month is kept as the monthly copy.
monthly="$BACKUP_DIR/monthly/household_budget-$month.dump"
if [[ ! -e "$monthly" ]]; then
    cp "$dump" "$monthly"
    log "saved monthly copy $monthly"
fi

# 3. Retention.
find "$BACKUP_DIR/daily" -name 'household_budget-*.dump' -mtime +"$((KEEP_DAILY - 1))" -print -delete \
    | sed 's/^/[backup] removed old daily /'
mapfile -t monthlies < <(ls -1 "$BACKUP_DIR/monthly"/household_budget-*.dump 2>/dev/null | sort)
excess=$(( ${#monthlies[@]} - KEEP_MONTHLY ))
for (( i = 0; i < excess; i++ )); do
    rm -- "${monthlies[$i]}"
    log "removed old monthly ${monthlies[$i]}"
done

# 4. Off-Pi copy.
if [[ -z "${REMOTE:-}" ]]; then
    log "ERROR: REMOTE is not set, so nothing was copied off the Pi"
    exit 1
fi
ssh_cmd="ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
[[ -n "${SSH_KEY:-}" ]] && ssh_cmd="$ssh_cmd -i $SSH_KEY"
log "copying to $REMOTE"
rsync -a --delete -e "$ssh_cmd" "$BACKUP_DIR/" "$REMOTE/db/"
if [[ -d "$ARCHIVE_DIR" ]]; then
    rsync -a -e "$ssh_cmd" "$ARCHIVE_DIR/" "$REMOTE/csv-archive/"
fi
log "done"
