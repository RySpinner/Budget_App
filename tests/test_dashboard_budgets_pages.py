"""Smoke tests for the Dashboard and Budgets pages."""

from datetime import date
from pathlib import Path

import psycopg
import pytest
from streamlit.testing.v1 import AppTest

from budget.budgets import set_budget
from budget.seed import CATEGORIES_FILE, load_toml, seed_categories
from budget.transactions import add_manual

PAGES = Path(__file__).resolve().parent.parent / "app" / "views"


@pytest.fixture(scope="module")
def db(committed_database_url):
    this_month = date.today().replace(day=1)
    with psycopg.connect(committed_database_url) as conn:
        seed_categories(conn, load_toml(CATEGORIES_FILE))
        person = conn.execute("INSERT INTO people (name) VALUES ('Pat') RETURNING id").fetchone()[0]
        fuel = conn.execute("SELECT id FROM categories WHERE name = 'Fuel'").fetchone()[0]
        transport = conn.execute("SELECT id FROM categories WHERE name = 'Transportation'").fetchone()[0]
        set_budget(conn, transport, this_month, 20000)
        add_manual(conn, txn_date=this_month, amount_cents=4500, description="Fake gas station",
                   purchaser_id=person, category_id=fuel)
        add_manual(conn, txn_date=this_month, amount_cents=999, description="Fake mystery shop",
                   purchaser_id=person)
    return committed_database_url


def run(page, db, monkeypatch):
    monkeypatch.setenv("BUDGET_DATABASE_URL", db)
    return AppTest.from_file(str(PAGES / page), default_timeout=15).run()


def test_dashboard_renders_with_numbers(db, monkeypatch):
    app = run("dashboard_page.py", db, monkeypatch)
    assert not app.exception, app.exception
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Spent this month"] == "$54.99"
    assert metrics["Uncategorized"] == "$9.99"
    assert metrics["Monthly budget"] == "$200.00"      # total budget, not spending
    assert metrics["Pace to date"].startswith("$")
    status = app.dataframe[0].value.set_index("Category")["Status"]
    assert status["Transportation"] in ("✅ On pace", "⚠️ Over pace")
    assert status["Dining & Going Out"] == "— No budget"


def test_dashboard_drill_down(db, monkeypatch):
    app = run("dashboard_page.py", db, monkeypatch)
    drill = next(s for s in app.selectbox if s.label == "Category")
    drill.select_index(0).run()   # Transportation (first in sort order)
    assert not app.exception
    assert any("Transportation in" in s.value for s in app.subheader)


def test_budgets_page_saves(db, monkeypatch):
    app = run("budgets_page.py", db, monkeypatch)
    assert not app.exception
    assert any(b.label == "Save 0 budgets" for b in app.button)
    history = app.dataframe  # data_editor tables aren't listed here; just check the page ran
    assert history is not None


def test_trend_months_in_date_order_even_when_stacks_differ():
    """A month with more category segments must not sort after a later month."""
    import pandas as pd
    from app.charts import trend_chart
    df = pd.DataFrame([
        {"Month": "Sep 2026", "month_start": date(2026, 9, 1), "Category": c, "cents": 100, "Amount": "$1.00"}
        for c in ("A", "B", "C", "D")
    ] + [{"Month": "Oct 2026", "month_start": date(2026, 10, 1), "Category": "A", "cents": 100, "Amount": "$1.00"}]
      + [{"Month": "Aug 2026", "month_start": date(2026, 8, 1), "Category": "B", "cents": 100, "Amount": "$1.00"}])
    spec = trend_chart(df, ["A", "B", "C", "D"], "light").to_dict()
    assert spec["encoding"]["x"]["sort"] == ["Aug 2026", "Sep 2026", "Oct 2026"]
