"""Smoke tests for the Accounts page (phase 11a)."""

from datetime import date
from pathlib import Path

import psycopg
import pytest
from streamlit.testing.v1 import AppTest

from budget.balances import save_snapshot, set_opening_balance

PAGE = Path(__file__).resolve().parent.parent / "app" / "views" / "accounts_page.py"


@pytest.fixture(scope="module")
def db(committed_database_url):
    with psycopg.connect(committed_database_url) as conn:
        person = conn.execute("INSERT INTO people (name) VALUES ('P') RETURNING id").fetchone()[0]
        bank = conn.execute(
            "INSERT INTO accounts (account_class, account_type, display_name, institution, owner_person_id, "
            "parser_key) VALUES ('asset', 'checking', 'Bank', 'Fake', %s, 'bcu') RETURNING id", (person,),
        ).fetchone()[0]
        set_opening_balance(conn, bank, 100000, date(2026, 10, 31))
        conn.execute(
            "INSERT INTO transactions (source, account_id, txn_date, amount_cents, raw_description, merchant, "
            "kind, kind_source, purchaser_id) VALUES ('manual', %s, '2026-11-01', 2500, 'FAKE', 'FAKE', "
            "'expense', 'manual', %s)", (bank, person),
        )
        save_snapshot(conn, bank, date(2026, 11, 2), 97000, "statement")     # $5.00 less than we have
    return committed_database_url


def run(db, monkeypatch):
    monkeypatch.setenv("BUDGET_DATABASE_URL", db)
    return AppTest.from_file(str(PAGE), default_timeout=10).run()


def choose_bank(app):
    next(s for s in app.selectbox if s.label == "Account details").select_index(
        next(i for i, o in enumerate(next(s for s in app.selectbox if s.label == "Account details").options)
             if o == "Bank")).run()
    return app


def test_list_shows_difference(db, monkeypatch):
    app = run(db, monkeypatch)
    assert not app.exception
    table = app.dataframe[0].value
    bank = table[table["Account"] == "Bank"].iloc[0]
    assert (bank["Balance today"], bank["Difference"], bank["Status"]) == ("$975.00", "-$5.00", "⚠ Off by $5.00")


def test_adjust_then_reconcile(db, monkeypatch):
    app = choose_bank(run(db, monkeypatch))
    assert not app.exception
    next(b for b in app.button if b.label.startswith("Add a balance adjustment of -$5.00")).click().run()
    assert "Balance adjustment added" in app.success[0].value
    app = choose_bank(app)
    next(b for b in app.button if b.label.startswith("Mark reconciled through")).click().run()
    assert "reconciled through Nov 2, 2026" in app.success[0].value
    with psycopg.connect(db) as conn:
        assert conn.execute("SELECT reconciled_through FROM accounts WHERE display_name = 'Bank'").fetchone()[0] \
            == date(2026, 11, 2)
        assert conn.execute("SELECT amount_cents FROM transactions WHERE kind = 'adjustment'").fetchone()[0] == 500


def test_add_account(db, monkeypatch):
    app = run(db, monkeypatch)
    next(t for t in app.text_input if t.label == "Name").input("Fake Savings")
    next(t for t in app.text_input if t.label == "Institution").input("Fake")
    next(b for b in app.button if b.label == "Add account").click().run()
    assert not app.exception
    assert "Added Fake Savings" in app.success[0].value
