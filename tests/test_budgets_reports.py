from datetime import date

import pytest

from budget.budgets import budget_history, delete_budget, set_budget
from budget.reports import (
    add_months, as_of_day, category_pace, category_trend, monthly_trend, subcategory_totals,
    uncategorized_cents,
)
from budget.transactions import SplitLine, set_splits


# ---- dates ----------------------------------------------------------------------

def test_add_months():
    assert add_months(date(2026, 11, 1), 2) == date(2027, 1, 1)
    assert add_months(date(2026, 1, 1), -1) == date(2025, 12, 1)
    assert add_months(date(2026, 1, 1), -13) == date(2024, 12, 1)


def test_as_of_day():
    today = date(2026, 11, 14)
    assert as_of_day(date(2026, 11, 1), today) == today
    assert as_of_day(date(2026, 10, 1), today) == date(2026, 10, 31)
    assert as_of_day(date(2027, 2, 1), today) is None


# ---- budgets -------------------------------------------------------------------

def test_set_budget_upserts_and_keeps_history(conn, basics):
    set_budget(conn, basics["top"], date(2026, 1, 15), 30000)   # stored as 2026-01-01
    set_budget(conn, basics["top"], date(2026, 1, 1), 35000)    # same month: replaced
    set_budget(conn, basics["top"], date(2026, 4, 1), 40000)
    rows = [(b.effective_from, b.monthly_amount_cents) for b in budget_history(conn) if b.category_id == basics["top"]]
    assert rows == [(date(2026, 4, 1), 40000), (date(2026, 1, 1), 35000)]
    q = lambda d: conn.execute("SELECT budget_for_month(%s, %s)", (basics["top"], d)).fetchone()[0]  # noqa: E731
    assert (q("2026-03-01"), q("2026-04-01")) == (35000, 40000)


def test_set_budget_rejects_negative_and_subcategory(conn, basics):
    with pytest.raises(ValueError):
        set_budget(conn, basics["top"], date(2026, 1, 1), -1)
    with pytest.raises(Exception):
        set_budget(conn, basics["sub"], date(2026, 1, 1), 100)


def test_delete_budget_restores_previous(conn, basics):
    set_budget(conn, basics["top"], date(2026, 1, 1), 100)
    set_budget(conn, basics["top"], date(2026, 3, 1), 200)
    newest = next(b.id for b in budget_history(conn) if b.effective_from == date(2026, 3, 1))
    delete_budget(conn, newest)
    assert conn.execute("SELECT budget_for_month(%s, '2026-05-01')", (basics["top"],)).fetchone()[0] == 100


# ---- reports -------------------------------------------------------------------

def test_category_pace_and_status(conn, add_card_txn, basics):
    set_budget(conn, basics["top"], date(2026, 1, 1), 31000)
    add_card_txn(amount_cents=12000, txn_date="2026-01-05", category_id=basics["sub"], category_source="manual")
    pace = {p.top_category_id: p for p in category_pace(conn, date(2026, 1, 1), date(2026, 1, 10))}
    p = pace[basics["top"]]
    assert (p.spent_cents, p.budget_cents, p.pace_cents) == (12000, 31000, 10000)
    assert p.status == "Over pace"

    add_card_txn(amount_cents=20000, txn_date="2026-01-06", category_id=basics["sub2"], category_source="manual")
    p = {x.top_category_id: x for x in category_pace(conn, date(2026, 1, 1), date(2026, 1, 31))}[basics["top"]]
    assert p.status == "Over budget"


def test_status_on_pace_and_no_budget(conn, add_card_txn, basics):
    add_card_txn(amount_cents=100, txn_date="2026-01-05", category_id=basics["sub"], category_source="manual")
    p = {x.top_category_id: x for x in category_pace(conn, date(2026, 1, 1), date(2026, 1, 31))}[basics["top"]]
    assert p.status == "No budget"
    set_budget(conn, basics["top"], date(2026, 1, 1), 31000)
    p = {x.top_category_id: x for x in category_pace(conn, date(2026, 1, 1), date(2026, 1, 31))}[basics["top"]]
    assert p.status == "On pace"


def test_future_month_has_no_pace(conn, basics):
    set_budget(conn, basics["top"], date(2026, 1, 1), 31000)
    p = {x.top_category_id: x for x in category_pace(conn, date(2026, 5, 1), None)}[basics["top"]]
    assert (p.budget_cents, p.pace_cents) == (31000, None)


def test_splits_count_in_their_own_categories(conn, add_card_txn, basics):
    tid = add_card_txn(amount_cents=1000, txn_date="2026-02-03")
    set_splits(conn, tid, [SplitLine(700, basics["sub"]), SplitLine(300, basics["sub2"])])
    subs = subcategory_totals(conn, date(2026, 2, 1), basics["top"])
    assert [(s.category_id, s.amount_cents) for s in subs] == [(basics["sub"], 700), (basics["sub2"], 300)]
    assert uncategorized_cents(conn, date(2026, 2, 1)) == 0


def test_uncategorized_and_trend(conn, add_card_txn, basics):
    add_card_txn(amount_cents=500, txn_date="2026-01-05")
    add_card_txn(amount_cents=250, txn_date="2026-02-05", category_id=basics["sub"], category_source="manual")
    add_card_txn(amount_cents=-50, txn_date="2026-02-06", category_id=basics["sub"], category_source="manual")
    assert uncategorized_cents(conn, date(2026, 1, 1)) == 500
    points = [(t.month, t.top_category_id, t.amount_cents) for t in monthly_trend(conn, date(2026, 2, 1), months=2)]
    assert points == [(date(2026, 1, 1), None, 500), (date(2026, 2, 1), basics["top"], 200)]
    # Older months fall outside a 1-month window.
    assert [t.month for t in monthly_trend(conn, date(2026, 2, 1), months=1)] == [date(2026, 2, 1)]


def test_category_trend_fills_empty_months(conn, add_card_txn, basics):
    set_budget(conn, basics["top"], date(2026, 2, 1), 9000)
    add_card_txn(amount_cents=300, txn_date="2026-03-02", category_id=basics["sub"], category_source="manual")
    rows = category_trend(conn, basics["top"], date(2026, 3, 1), months=3)
    assert rows == [(date(2026, 1, 1), 0, None), (date(2026, 2, 1), 0, 9000), (date(2026, 3, 1), 300, 9000)]
