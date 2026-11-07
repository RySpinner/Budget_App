"""Migration 0004 (cards -> accounts) must carry v1 data over intact."""

import os
import uuid

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from budget.migrate import apply_migrations


@pytest.fixture
def v1_database():
    admin_url = os.environ.get("TEST_DATABASE_URL")
    if not admin_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    name = f"budget_m4_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    params = conninfo_to_dict(admin_url)
    params["dbname"] = name
    url = make_conninfo(**params)
    yield url
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


def test_v1_data_survives_migration(v1_database):
    with psycopg.connect(v1_database) as conn:
        apply_migrations(conn, up_to="0003")
        q = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
        person = q("INSERT INTO people (name) VALUES ('Pat') RETURNING id")
        card = q("INSERT INTO cards (display_name, issuer, last4, owner_person_id, parser_key) "
                 "VALUES ('Pat Citi', 'Citi', '1234', %s, 'citi') RETURNING id", person)
        batch = q("INSERT INTO import_batches (card_id, filename, file_sha256, rows_in_file, rows_inserted, "
                  "rows_duplicate, rows_excluded) VALUES (%s, 'x.csv', %s, 1, 1, 0, 0) RETURNING id", card, "b" * 64)
        top = q("INSERT INTO categories (name) VALUES ('Food') RETURNING id")
        sub = q("INSERT INTO categories (name, parent_id) VALUES ('Coffee', %s) RETURNING id", top)
        rule = q("INSERT INTO rules (match_type, pattern, card_id, category_id) VALUES ('contains', 'CAFE', %s, %s) RETURNING id", card, sub)
        card_txn = q("INSERT INTO transactions (source, card_id, import_batch_id, txn_date, amount_cents, raw_description, "
                     "purchaser_id, fingerprint, category_id, category_source, rule_id) "
                     "VALUES ('card', %s, %s, '2026-10-01', 450, 'FAKE CAFE', %s, 'fp1', %s, 'rule', %s) RETURNING id",
                     card, batch, person, sub, rule)
        manual_txn = q("INSERT INTO transactions (source, txn_date, amount_cents, raw_description, purchaser_id, "
                       "category_id, category_source) VALUES ('manual', '2026-10-02', 2000, 'Market', %s, %s, 'manual') RETURNING id",
                       person, sub)
        conn.execute("INSERT INTO budgets (category_id, monthly_amount_cents, effective_from) VALUES (%s, 10000, '2026-10-01')", (top,))
        conn.commit()

        assert apply_migrations(conn, up_to="0004") == ["0004_accounts.sql"]

        acct = conn.execute("SELECT id, display_name, institution, account_class, account_type, parser_key "
                            "FROM accounts WHERE id = %s", (card,)).fetchone()
        assert acct == (card, "Pat Citi", "Citi", "liability", "credit_card", "citi")
        cash = conn.execute("SELECT id, account_class, account_type, parser_key FROM accounts WHERE display_name = 'Cash'").fetchone()
        assert cash[1:] == ("asset", "cash", None)
        assert q("SELECT count(*) FROM people WHERE name = 'Joint'") == 1

        assert conn.execute("SELECT source, account_id, kind, kind_source, category_id FROM transactions WHERE id = %s",
                            (card_txn,)).fetchone() == ("import", card, "expense", "parser", sub)
        assert conn.execute("SELECT source, account_id, kind, kind_source FROM transactions WHERE id = %s",
                            (manual_txn,)).fetchone() == ("manual", cash[0], "expense", "manual")
        assert q("SELECT account_id FROM rules WHERE id = %s", rule) == card
        assert q("SELECT account_id FROM import_batches WHERE id = %s", batch) == card
        assert q("SELECT kind FROM categories WHERE id = %s", sub) == "expense"
        # Reports still see both rows as spending.
        assert q("SELECT sum(amount_cents) FROM spending_lines") == 2450
        assert q("SELECT budget_for_month(%s, '2026-10-15')", top) == 10000

