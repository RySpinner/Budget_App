"""Smoke tests for the Review page's tabs (phase 10): counts, linking from the
Unmatched transfers tab, and unlinking a recent link."""

from pathlib import Path

import psycopg
import pytest
from streamlit.testing.v1 import AppTest

from budget.importer import commit_import, preview_import
from budget.seed import CATEGORIES_FILE, load_toml, seed_categories

PAGE = Path(__file__).resolve().parent.parent / "app" / "views" / "review_page.py"
CITI = Path(__file__).parent / "fixtures" / "citi" / "sample.csv"


@pytest.fixture(scope="module")
def db(committed_database_url, tmp_path_factory):
    with psycopg.connect(committed_database_url) as conn:
        seed_categories(conn, load_toml(CATEGORIES_FILE))
        person = conn.execute("INSERT INTO people (name) VALUES ('P') RETURNING id").fetchone()[0]
        card = conn.execute(
            "INSERT INTO accounts (account_class, account_type, display_name, institution, owner_person_id, "
            "parser_key) VALUES ('liability', 'credit_card', 'Citi', 'Citi', %s, 'citi') RETURNING id", (person,),
        ).fetchone()[0]
        # Two card payments ($500 and $250) with nothing to link to yet.
        commit_import(conn, preview_import(conn, card, "s.csv", CITI.read_bytes()),
                      archive=tmp_path_factory.mktemp("a"))
        cash = conn.execute("SELECT id FROM accounts WHERE account_type = 'cash'").fetchone()[0]
        conn.execute(
            "INSERT INTO transactions (source, account_id, txn_date, amount_cents, raw_description, merchant, "
            "kind, kind_source, purchaser_id, needs_care) "
            "VALUES ('manual', %s, '2026-10-20', 4000, 'CHECK 1001', 'CHECK 1001', 'expense', 'manual', %s, "
            "'paper check')", (cash, person),
        )
    return committed_database_url


def run(db, monkeypatch):
    monkeypatch.setenv("BUDGET_DATABASE_URL", db)
    return AppTest.from_file(str(PAGE), default_timeout=10).run()


def test_tabs_show_counts(db, monkeypatch):
    app = run(db, monkeypatch)
    assert not app.exception
    assert [t.label for t in app.tabs] == ["Uncategorized (5)", "Unmatched transfers (2)", "Needs care (1)"]


def test_unmatched_leg_offers_new_cash_entry_and_categories(db, monkeypatch):
    app = run(db, monkeypatch)
    options = app.selectbox(key=next(s.key for s in app.selectbox if s.label == "Other side")).options
    assert options[0] == "Leave for now"
    assert "New entry in Cash" in options
    assert any(o.startswith("Not a transfer: ") for o in options)


def test_create_cash_side_then_unlink(db, monkeypatch):
    app = run(db, monkeypatch)
    next(s for s in app.selectbox if s.label == "Other side").select("New entry in Cash").run()
    next(b for b in app.button if b.key == "save_transfers").click().run()
    assert not app.exception
    assert "Saved 1 choice." in app.success[0].value
    assert [t.label for t in app.tabs][1] == "Unmatched transfers (1)"
    with psycopg.connect(db) as conn:
        assert conn.execute("SELECT merchant, amount_cents FROM transactions WHERE generated_from_id IS NOT NULL"
                            ).fetchone() == ("To Citi", 50000)

    next(b for b in app.button if b.label == "Unlink").click().run()
    assert "Unlinked" in app.success[0].value
    assert [t.label for t in app.tabs][1] == "Unmatched transfers (2)"
    with psycopg.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM transactions WHERE generated_from_id IS NOT NULL").fetchone()[0] == 0
