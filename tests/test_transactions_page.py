"""Smoke tests for the Transactions page. Editing table cells isn't possible
in AppTest; that logic is covered in test_transactions.py."""

from pathlib import Path

import psycopg
import pytest
from streamlit.testing.v1 import AppTest

from budget.importer import commit_import, preview_import
from budget.seed import CATEGORIES_FILE, load_toml, seed_categories

PAGE = str(Path(__file__).resolve().parent.parent / "app" / "views" / "transactions_page.py")
FIXTURE = Path(__file__).parent / "fixtures" / "citi" / "sample.csv"


@pytest.fixture(scope="module")
def db(committed_database_url, tmp_path_factory):
    with psycopg.connect(committed_database_url) as conn:
        seed_categories(conn, load_toml(CATEGORIES_FILE))
        person = conn.execute("INSERT INTO people (name) VALUES ('Pat') RETURNING id").fetchone()[0]
        card = conn.execute(
            "INSERT INTO accounts (account_class, account_type, display_name, institution, owner_person_id, parser_key) "
            "VALUES ('liability', 'credit_card', 'Citi', 'Citi', %s, 'citi') RETURNING id", (person,),
        ).fetchone()[0]
        commit_import(conn, preview_import(conn, card, "s.csv", FIXTURE.read_bytes()),
                      archive=tmp_path_factory.mktemp("a"))
    return committed_database_url


@pytest.fixture
def app(db, monkeypatch):
    monkeypatch.setenv("BUDGET_DATABASE_URL", db)
    return AppTest.from_file(PAGE, default_timeout=10).run()


def test_renders_and_totals(app):
    assert not app.exception
    month = next(s for s in app.selectbox if s.label == "Month")
    month.select_index(0).run()   # "All months": the fixture is October 2026
    assert not app.exception
    total = next(m for m in app.metric if m.label == "Total shown")
    assert total.value == "$1,059.91"   # 42.17 + 4.50 + 4.50 + 1023.99 - 15.25


def test_manual_entry_form(app, db):
    form_inputs = {t.label: t for t in app.text_input}
    form_inputs["Amount"].input("12.34")
    form_inputs["Description"].input("Farmers market")
    next(b for b in app.button if b.label == "Add entry").click().run()
    assert not app.exception
    assert "Added Farmers market (\\$12.34)" in app.success[0].value   # "$" escaped for markdown
    with psycopg.connect(db) as conn:
        row = conn.execute(
            "SELECT t.source, t.amount_cents, a.display_name FROM transactions t JOIN accounts a ON a.id = t.account_id "
            "WHERE t.raw_description = 'Farmers market'"
        ).fetchone()
    assert row == ("manual", 1234, "Cash")   # manual entries go in the Cash account by default


def test_bad_manual_amount_shows_error(app):
    form_inputs = {t.label: t for t in app.text_input}
    form_inputs["Amount"].input("12.345")
    form_inputs["Description"].input("Bad")
    next(b for b in app.button if b.label == "Add entry").click().run()
    assert "fractions of a cent" in app.error[0].value


def test_manual_income_entry_is_money_in(db, monkeypatch):
    """Interest entered as -5.00 (money in) into an account with no CSV adds $5 to its balance."""
    from datetime import date

    from budget.balances import balance_on, set_opening_balance

    with psycopg.connect(db) as conn:
        person = conn.execute("SELECT id FROM people WHERE name = 'Pat'").fetchone()[0]
        savings = conn.execute(
            "INSERT INTO accounts (account_class, account_type, display_name, institution, owner_person_id) "
            "VALUES ('asset', 'savings', 'Test savings', 'Fake', %s) RETURNING id", (person,),
        ).fetchone()[0]
        set_opening_balance(conn, savings, 100000, date(2026, 10, 31))
    monkeypatch.setenv("BUDGET_DATABASE_URL", db)
    app = AppTest.from_file(PAGE, default_timeout=10).run()
    form_inputs = {t.label: t for t in app.text_input}
    form_inputs["Amount"].input("-5.00")
    form_inputs["Description"].input("Fake interest")
    next(s for s in app.selectbox if s.label == "Category" and "Income › Interest" in s.options
         ).select("Income › Interest")
    next(s for s in app.selectbox if s.label == "Account" and "All accounts" not in s.options).select(savings)
    app.date_input[0].set_value(date(2026, 11, 10))
    next(b for b in app.button if b.label == "Add entry").click().run()
    assert not app.exception
    with psycopg.connect(db) as conn:
        assert conn.execute("SELECT kind, amount_cents FROM transactions WHERE raw_description = 'Fake interest'"
                            ).fetchone() == ("income", -500)
        assert balance_on(conn, savings, date(2026, 11, 30)) == 100500


def test_edit_manual_entry_account(db, monkeypatch):
    """A manual entry made in the wrong account can be moved with the edit form."""
    from datetime import date

    with psycopg.connect(db) as conn:
        person = conn.execute("SELECT id FROM people WHERE name = 'Pat'").fetchone()[0]
        cash = conn.execute("SELECT id FROM accounts WHERE account_type = 'cash'").fetchone()[0]
        moved_to = conn.execute(
            "INSERT INTO accounts (account_class, account_type, display_name, institution, owner_person_id) "
            "VALUES ('asset', 'savings', 'Edit target', 'Fake', %s) RETURNING id", (person,),
        ).fetchone()[0]
        tid = conn.execute(
            "INSERT INTO transactions (source, account_id, txn_date, amount_cents, raw_description, merchant, "
            "kind, kind_source, purchaser_id) VALUES ('manual', %s, %s, 700, 'Wrong place', 'Wrong place', "
            "'expense', 'manual', %s) RETURNING id", (cash, date.today(), person),
        ).fetchone()[0]
    monkeypatch.setenv("BUDGET_DATABASE_URL", db)
    app = AppTest.from_file(PAGE, default_timeout=10).run()
    picker = next(s for s in app.selectbox if s.label == "Transaction (from the rows above)")
    picker.select(tid).run()
    assert not app.exception
    app.selectbox(key=f"edit_account_{tid}").select(moved_to)
    app.text_input(key=f"edit_amount_{tid}").input("7.25")
    next(b for b in app.button if b.label == "Save changes").click().run()
    assert not app.exception
    assert "Saved Wrong place." in app.success[0].value
    with psycopg.connect(db) as conn:
        assert conn.execute("SELECT account_id, amount_cents FROM transactions WHERE id = %s", (tid,)).fetchone() \
            == (moved_to, 725)
