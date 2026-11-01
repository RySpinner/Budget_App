"""Opening database connections."""

import psycopg

from budget.config import database_conninfo


def connect(conninfo: str | None = None) -> psycopg.Connection:
    """Open a connection. Uses the configured database unless one is given."""
    return psycopg.connect(conninfo or database_conninfo())
