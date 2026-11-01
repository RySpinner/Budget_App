"""Where to find the database.

Order of precedence:
  1. The BUDGET_DATABASE_URL environment variable (used by tests and one-off scripts).
  2. config/secrets.toml (gitignored; copy config/secrets.example.toml to start).
"""

import os
import tomllib
from pathlib import Path

from psycopg.conninfo import make_conninfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SECRETS_FILE = PROJECT_ROOT / "config" / "secrets.toml"


def database_conninfo() -> str:
    url = os.environ.get("BUDGET_DATABASE_URL")
    if url:
        return url

    if not SECRETS_FILE.exists():
        raise RuntimeError(
            f"No database configured. Set BUDGET_DATABASE_URL or create {SECRETS_FILE} "
            "(see config/secrets.example.toml)."
        )

    with SECRETS_FILE.open("rb") as f:
        db = tomllib.load(f)["database"]

    return make_conninfo(
        host=db.get("host", "localhost"),
        port=db.get("port", 5432),
        dbname=db["dbname"],
        user=db["user"],
        password=db.get("password"),
    )
