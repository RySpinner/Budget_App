"""Apply versioned schema migrations.

Migrations are plain SQL files in migrations/, named NNNN_description.sql.
They run in filename order. Each one runs inside its own transaction and is
recorded in the schema_migrations table, so it is applied exactly once.

Rules for writing migrations:
  * Never edit a migration that has already been applied anywhere.
    Make a new file instead.
  * Don't put BEGIN/COMMIT in the file; the runner wraps it for you.

Usage:
    python -m budget.migrate          # apply anything pending
    python -m budget.migrate --status # list applied / pending
"""

import argparse
import re
from pathlib import Path

import psycopg

from budget.config import PROJECT_ROOT
from budget.db import connect

MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
FILENAME_PATTERN = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")

# Arbitrary fixed number so two people running migrate at once don't collide.
MIGRATION_LOCK_ID = 727_001


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    files = sorted(p for p in directory.glob("*.sql"))
    for p in files:
        if not FILENAME_PATTERN.match(p.name):
            raise ValueError(f"Migration file name {p.name!r} must look like 0001_some_name.sql")
    versions = [p.name[:4] for p in files]
    if len(versions) != len(set(versions)):
        raise ValueError("Two migration files share the same number")
    return files


def ensure_migrations_table(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    TEXT PRIMARY KEY,
            filename   TEXT NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    conn.commit()


def applied_versions(conn: psycopg.Connection) -> set[str]:
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {r[0] for r in rows}


def apply_migrations(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR, up_to: str | None = None) -> list[str]:
    """Apply pending migrations (all, or only up to and including version `up_to`, e.g. "0003").

    Returns the filenames that were applied.
    """
    ensure_migrations_table(conn)
    applied_now = []

    for path in migration_files(directory):
        version = path.name[:4]
        if up_to is not None and version > up_to:
            break
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_ID,))
            if version in applied_versions(conn):
                continue
            conn.execute(path.read_text())
            conn.execute(
                "INSERT INTO schema_migrations (version, filename) VALUES (%s, %s)",
                (version, path.name),
            )
        applied_now.append(path.name)

    return applied_now


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--status", action="store_true", help="show applied and pending migrations")
    args = parser.parse_args()

    with connect() as conn:
        if args.status:
            ensure_migrations_table(conn)
            done = applied_versions(conn)
            for path in migration_files():
                mark = "applied" if path.name[:4] in done else "PENDING"
                print(f"{mark:8} {path.name}")
            return

        applied = apply_migrations(conn)
        if applied:
            for name in applied:
                print(f"applied  {name}")
        else:
            print("Database is up to date.")


if __name__ == "__main__":
    main()
