"""Cash flow, net worth and the books check (phase 11b)."""

from datetime import date

import pytest

from budget.balances import add_adjustment, set_opening_balance
from budget.reports import books_check, cash_flow, net_worth, net_worth_series
from budget.transactions import SplitLine, set_splits
from budget.transfers import link

D = date


@pytest.fixture
def accts(conn, basics):
    q = lambda sql, *args: conn.execute(sql, args).fetchone()[0]  # noqa: E731
    bank = q("INSERT INTO accounts (display_name, institution, owner_person_id, parser_key, account_class, "
             "account_type) VALUES ('Bank', 'Fake', %s, 'bcu', 'asset', 'checking') RETURNING id", basics["person"])
    savings = q("INSERT INTO accounts (display_name, institution, owner_person_id, parser_key, account_class, "
                "account_type) VALUES ('Savings', 'Fake', %s, 'bcu', 'asset', 'savings') RETURNING id",
                basics["person"])
    return {"bank": bank, "card": basics["card"], "savings": savings}


@pytest.fixture
def add(add_card_txn, basics):
    """add(account, amount, txn_date, posted_date=None, kind='expense', category=None) -> id."""
    def _add(account_id, amount_cents, txn_date, posted_date=None, kind="expense", category=None, **extra):
        return add_card_txn(account_id=account_id, amount_cents=amount_cents, txn_date=txn_date,
                            posted_date=posted_date, kind=kind, kind_source="parser" if kind else None,
                            category_id=category, category_source="rule" if category else None, **extra)
    return _add


def test_cash_flow_counts_income_and_spending_only(conn, accts, add, basics):
    add(accts["bank"], -200000, "2026-11-01", kind="income", category=basics["income"])   # paycheck
    add(accts["card"], 5000, "2026-11-03")                                                # purchase
    add(accts["card"], -1000, "2026-11-04")                                               # refund
    add(accts["card"], -50000, "2026-11-05", kind="transfer")                             # card payment
    add(accts["bank"], 700, "2026-11-06", kind=None)                                      # still in Review
    set_opening_balance(conn, accts["bank"], 0, D(2026, 10, 31))
    add_adjustment(conn, accts["bank"], D(2026, 11, 7), 300)
    nov = cash_flow(conn, D(2026, 11, 1), months=2)
    assert [m.month for m in nov] == [D(2026, 10, 1), D(2026, 11, 1)]
    m = nov[1]
    assert (m.income_cents, m.spending_cents, m.saved_cents) == (200000, 4000, 196000)
    assert (m.unsorted_cents, m.unsorted_count) == (-700, 1)
    assert nov[0].saved_cents == 0


def test_cash_flow_uses_split_lines(conn, accts, add, basics):
    txn = add(accts["card"], 3000, "2026-11-03", category=basics["sub"])
    set_splits(conn, txn, [SplitLine(2000, basics["sub"]), SplitLine(1000, basics["sub2"])])
    assert cash_flow(conn, D(2026, 11, 1), months=1)[0].spending_cents == 3000


def test_net_worth(conn, accts, add):
    set_opening_balance(conn, accts["bank"], 100000, D(2026, 10, 31))      # $1,000 held
    set_opening_balance(conn, accts["card"], 20000, D(2026, 10, 31))       # $200 owed
    set_opening_balance(conn, accts["savings"], 500000, D(2026, 11, 15))   # starts mid-November
    add(accts["card"], 5000, "2026-11-03")
    nw = net_worth(conn, D(2026, 11, 10))
    assert (nw.assets_cents, nw.liabilities_cents, nw.net_cents, nw.accounts_not_started) == (100000, 25000, 75000, 1)
    assert net_worth(conn, D(2026, 11, 30)).net_cents == 575000
    series = net_worth_series(conn, D(2026, 11, 1), today=D(2026, 11, 20), months=2)
    assert [(p.as_of, p.net_cents) for p in series] == [(D(2026, 10, 31), 80000), (D(2026, 11, 20), 575000)]


def test_books_check_adds_up_exactly(conn, accts, add, basics):
    bank, card, savings = accts["bank"], accts["card"], accts["savings"]
    set_opening_balance(conn, bank, 100000, D(2026, 10, 31))
    add(bank, -5, "2026-10-20", kind="income", category=basics["income"])     # before the opening: history only
    add(card, 4000, "2026-10-30", "2026-11-02")                                 # bought Oct 30, posted Nov 2
    add(bank, -200000, "2026-11-01", kind="income", category=basics["income"])  # paycheck
    add(card, 5000, "2026-11-10")
    out = add(bank, 30000, "2026-11-29", kind="transfer")                      # pays the card...
    arrives = add(card, -30000, "2026-12-01", kind="transfer")                 # ...posted there on Dec 1
    link(conn, out, arrives)
    add(bank, 700, "2026-11-12", kind=None)                                     # still in Review
    add_adjustment(conn, bank, D(2026, 11, 20), -250)                          # bank had $2.50 less
    set_opening_balance(conn, savings, 500000, D(2026, 11, 15))                # savings starts mid-month
    add(savings, -1000, "2026-11-30", kind="income", category=basics["income"])  # interest

    nov = books_check(conn, D(2026, 11, 1))
    assert (nov.income_cents, nov.spending_cents, nov.adjustments_cents) == (201000, 9000, -250)
    assert (nov.transfers_cents, nov.unsorted_cents, nov.openings_cents) == (-30000, -700, 500000)
    assert nov.unexplained_cents == 0
    dec = books_check(conn, D(2026, 12, 1))
    assert dec.transfers_cents == 30000             # the other half of the in-transit payment
    assert dec.unexplained_cents == 0
    oct_ = books_check(conn, D(2026, 10, 1))
    assert (oct_.openings_cents, oct_.income_cents, oct_.unexplained_cents) == (100000, 0, 0)

    # The cash-flow view follows purchase dates instead, like budgets.
    assert cash_flow(conn, D(2026, 10, 1), months=1)[0].spending_cents == 4000


def test_books_check_can_stop_early(conn, accts, add, basics):
    set_opening_balance(conn, accts["bank"], 0, D(2026, 10, 31))
    add(accts["bank"], -1000, "2026-11-05", kind="income", category=basics["income"])
    add(accts["bank"], -2000, "2026-11-25", kind="income", category=basics["income"])
    part = books_check(conn, D(2026, 11, 1), through=D(2026, 11, 15))
    assert (part.income_cents, part.net_worth_change_cents, part.unexplained_cents) == (1000, 1000, 0)
