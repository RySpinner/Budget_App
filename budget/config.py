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


def secrets_file() -> Path:
    """config/secrets.toml, unless BUDGET_SECRETS_FILE points somewhere else (used by tests)."""
    override = os.environ.get("BUDGET_SECRETS_FILE")
    return Path(override) if override else SECRETS_FILE


def _load_secrets() -> dict:
    path = secrets_file()
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def archive_dir() -> Path:
    """Where original CSV files are archived (named by batch id).

    BUDGET_ARCHIVE_DIR overrides [paths] archive_dir in secrets.toml;
    the default is ./archive in the project (gitignored).
    """
    configured = os.environ.get("BUDGET_ARCHIVE_DIR") or _load_secrets().get("paths", {}).get("archive_dir")
    if not configured:
        return PROJECT_ROOT / "archive"
    path = Path(configured).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def app_settings() -> dict:
    """The [app] section of secrets.toml: password_hash, cookie_secret, require_password."""
    return _load_secrets().get("app", {})


def database_conninfo() -> str:
    url = os.environ.get("BUDGET_DATABASE_URL")
    if url:
        return url

    if not secrets_file().exists():
        raise RuntimeError(
            f"No database configured. Set BUDGET_DATABASE_URL or create {secrets_file()} "
            "(see config/secrets.example.toml)."
        )

    db = _load_secrets()["database"]

    return make_conninfo(
        host=db.get("host", "localhost"),
        port=db.get("port", 5432),
        dbname=db["dbname"],
        user=db["user"],
        password=db.get("password"),
    )
