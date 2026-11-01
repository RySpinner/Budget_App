"""Test setup: a throwaway database per test run.

Set TEST_DATABASE_URL to a server where you can create databases, e.g.
    TEST_DATABASE_URL=postgresql://postgres@localhost:5432/postgres pytest
A database named budget_test_<random> is created, migrated, and dropped
afterwards. Your real database is never touched.
"""

import os
import uuid

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from budget.migrate import apply_migrations


@pytest.fixture(scope="session")
def database_url():
    admin_url = os.environ.get("TEST_DATABASE_URL")
    if not admin_url:
        pytest.skip("TEST_DATABASE_URL is not set")

    name = f"budget_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')

    params = conninfo_to_dict(admin_url)
    params["dbname"] = name
    url = make_conninfo(**params)

    with psycopg.connect(url) as conn:
        apply_migrations(conn)

    yield url

    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


@pytest.fixture
def conn(database_url):
    """A connection whose work is rolled back after each test."""
    with psycopg.connect(database_url) as c:
        yield c
        c.rollback()


@pytest.fixture
def basics(conn):
    """Minimal fake data: a person, a card, a batch, and a small category tree."""
    q = lambda sql, *args: conn.execute(sql, args).fetchone()[0]  # noqa: E731
    person = q("INSERT INTO people (name) VALUES ('Test Person') RETURNING id")
    card = q(
        "INSERT INTO cards (display_name, issuer, last4, owner_person_id, parser_key) "
        "VALUES ('Test Card', 'Fake Bank', '9999', %s, 'fake') RETURNING id",
        person,
    )
    batch = q(
        "INSERT INTO import_batches (card_id, filename, file_sha256, rows_in_file, rows_inserted, "
        "rows_duplicate, rows_excluded) VALUES (%s, 'fake.csv', %s, 0, 0, 0, 0) RETURNING id",
        card, "a" * 64,
    )
    top = q("INSERT INTO categories (name) VALUES ('Test Top') RETURNING id")
    sub = q("INSERT INTO categories (name, parent_id) VALUES ('Test Sub', %s) RETURNING id", top)
    sub2 = q("INSERT INTO categories (name, parent_id) VALUES ('Test Sub 2', %s) RETURNING id", top)
    return {"person": person, "card": card, "batch": batch, "top": top, "sub": sub, "sub2": sub2}


@pytest.fixture
def add_card_txn(conn, basics):
    """Insert a card transaction with sensible fake defaults; returns its id."""
    counter = iter(range(1, 10_000))

    def _add(amount_cents=1234, txn_date="2026-01-15", posted_date="2026-01-16", **extra):
        cols = {
            "source": "card",
            "card_id": basics["card"],
            "import_batch_id": basics["batch"],
            "txn_date": txn_date,
            "posted_date": posted_date,
            "amount_cents": amount_cents,
            "raw_description": "FAKE MERCHANT 123",
            "purchaser_id": basics["person"],
            "fingerprint": f"fp-{next(counter)}",
            **extra,
        }
        names = ", ".join(cols)
        placeholders = ", ".join(["%s"] * len(cols))
        return conn.execute(
            f"INSERT INTO transactions ({names}) VALUES ({placeholders}) RETURNING id",
            list(cols.values()),
        ).fetchone()[0]

    return _add
