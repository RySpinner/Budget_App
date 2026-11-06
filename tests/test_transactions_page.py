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
