#!/usr/bin/env bash
# Replace the live database with a backup. Run on the Pi as root:
#   sudo /opt/household-budget/deploy/restore.sh /var/backups/household-budget/daily/household_budget-2026-11-01.dump
#
# Stops the app, recreates the database from the dump, and starts the app again.
# Optional second argument: database name (default household_budget).
# Set SKIP_SERVICE=1 to leave the app service alone (used by tests).
set -euo pipefail

dump="${1:?usage: restore.sh DUMP_FILE [DB_NAME]}"
db="${2:-household_budget}"
owner="${DB_OWNER:-budget_app}"
as_postgres() { runuser -u postgres -- "$@"; }

[[ -r "$dump" ]] || { echo "Can't read $dump"; exit 1; }
pg_restore --list "$dump" > /dev/null || { echo "$dump is not a readable pg_dump file"; exit 1; }

if [[ "${ASSUME_YES:-}" != "1" ]]; then
    read -r -p "This REPLACES database '$db' with $dump. Type the database name to continue: " answer
    [[ "$answer" == "$db" ]] || { echo "Cancelled."; exit 1; }
fi

[[ "${SKIP_SERVICE:-}" == "1" ]] || systemctl stop household-budget
as_postgres dropdb --if-exists "$db"
as_postgres createdb --owner="$owner" "$db"
as_postgres pg_restore --no-owner --role="$owner" --dbname="$db" "$dump"
[[ "${SKIP_SERVICE:-}" == "1" ]] || systemctl start household-budget
echo "Restored $db from $dump."
