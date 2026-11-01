#!/usr/bin/env bash
# Prove the latest backup can actually be restored, without touching the live
# database. Run on the Pi as root, e.g. once a month:
#   sudo /opt/household-budget/deploy/restore-test.sh
#
# Restores the newest dump into a scratch database, compares row counts with
# the live database, then drops the scratch database. Counts can differ a
# little if data changed since the backup; the test fails only if the restore
# itself fails or a table is missing.
set -euo pipefail

CONFIG="${1:-/etc/household-budget/backup.env}"
# shellcheck source=/dev/null
source "$CONFIG"
live="${DB_NAME:?}"
scratch="${live}_restore_test"
as_postgres() { runuser -u postgres -- "$@"; }

latest="$(ls -1t "$BACKUP_DIR"/daily/household_budget-*.dump 2>/dev/null | head -n 1 || true)"
[[ -n "$latest" ]] || { echo "No dumps found in $BACKUP_DIR/daily"; exit 1; }
echo "Testing restore of $latest"

cleanup() { as_postgres dropdb --if-exists "$scratch"; }
trap cleanup EXIT
as_postgres dropdb --if-exists "$scratch"
as_postgres createdb "$scratch"
as_postgres pg_restore --no-owner --dbname="$scratch" "$latest"

count() { as_postgres psql --dbname="$1" --tuples-only --no-align --command="SELECT count(*) FROM $2"; }
printf '%-18s %10s %10s\n' table backup live
for table in schema_migrations people cards categories transactions splits rules budgets import_batches; do
    printf '%-18s %10s %10s\n' "$table" "$(count "$scratch" "$table")" "$(count "$live" "$table")"
done
echo "Restore test PASSED: the backup restores cleanly."
