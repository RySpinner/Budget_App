"""Account balances and reconciliation (phase 11a)."""

from datetime import date

import pytest

from budget.balances import (
    account_summaries, add_adjustment, adjustment_amount_cents, balance_on, delete_snapshot, earliest_snapshot,
    reconciled_rows_in_batch, reconciled_through_if_affected, reconciliation_history, save_account,
    save_snapshot, set_opening_balance, set_reconciled_through,
)
from budget.rules import categorize_manually
from budget.transactions import add_manual, delete_manual, mark_as_transfer
from budget.transfers import candidates

D = date


@pytest.fixture
def bank(conn, basics):
    return conn.execute(
        "INSERT INTO accounts (display_name, institution, owner_person_id, parser_key, account_class, account_type) "
        "VALUES ('Bank', 'Fake', %s, 'bcu', 'asset', 'checking') RETURNING id", (basics["person"],),
    ).fetchone()[0]


@pytest.fixture
def add(add_card_txn):
    def _add(account_id, amount_cents, txn_date, posted_date=None, **extra):
        return add_card_txn(account_id=account_id, amount_cents=amount_cents, txn_date=txn_date,
                            posted_date=posted_date, **extra)
    return _add


def snapshot(conn, account_id, day, cents, source="statement"):
    save_snapshot(conn, account_id, day, cents, source)


# ---- computing balances ------------------------------------------------------

def test_asset_balance_from_opening_and_rows(conn, bank, add):
    set_opening_balance(conn, bank, 100000, D(2026, 10, 31))
    add(bank, 99999, "2026-10-31")                 # on the opening day: already in the opening balance
    add(bank, 2500, "2026-11-02")                  # $25 out
    add(bank, -50000, "2026-11-03")                # $500 in
    assert balance_on(conn, bank, D(2026, 10, 30)) is None      # before the opening balance: unknown
    assert balance_on(conn, bank, D(2026, 10, 31)) == 100000
    assert balance_on(conn, bank, D(2026, 11, 2)) == 97500
    assert balance_on(conn, bank, D(2026, 11, 30)) == 147500


def test_card_balance_is_money_owed(conn, basics, add):
    card = basics["card"]
    set_opening_balance(conn, card, 20000, D(2026, 10, 31))     # $200 owed
    add(card, 5000, "2026-11-01")                               # $50 charge
    add(card, -20000, "2026-11-05", kind="transfer")            # $200 payment
    assert balance_on(conn, card, D(2026, 11, 30)) == 5000


def test_no_opening_balance_starts_at_zero(conn, bank, add):
    add(bank, -1000, "2026-11-01")
    assert balance_on(conn, bank, D(2026, 11, 1)) == 1000


def test_balance_follows_the_posted_date(conn, basics, add):
    card = basics["card"]
    add(card, 5000, "2026-10-30", "2026-11-02")      # bought on the 30th, posted on the 2nd
    assert balance_on(conn, card, D(2026, 10, 31)) == 0
    assert balance_on(conn, card, D(2026, 11, 2)) == 5000


# ---- reconciliation and adjustments --------------------------------------------

def test_reconciliation_shows_difference(conn, bank, add):
    set_opening_balance(conn, bank, 100000, D(2026, 10, 31))
    add(bank, 2500, "2026-11-02")
    snapshot(conn, bank, D(2026, 11, 2), 97000)       # bank says $970; we have $975
    [r] = reconciliation_history(conn, bank)
    assert (r.bank_cents, r.computed_cents, r.difference_cents) == (97000, 97500, -500)


@pytest.mark.parametrize("account_class, difference, amount", [
    ("asset", -500, 500),        # bank holds $5 less: like $5 going out
    ("asset", 500, -500),
    ("liability", 500, 500),     # bank says $5 more owed: like a $5 charge
    ("liability", -500, -500),
])
def test_adjustment_amount_sign(account_class, difference, amount):
    assert adjustment_amount_cents(account_class, difference) == amount


@pytest.mark.parametrize("which", ["bank", "card"])
def test_adjustment_closes_the_difference(conn, bank, basics, add, which):
    account = bank if which == "bank" else basics["card"]
    set_opening_balance(conn, account, 10000, D(2026, 10, 31))
    add(account, 2500, "2026-11-02")
    snapshot(conn, account, D(2026, 11, 2), 12345)
    diff = reconciliation_history(conn, account)[0].difference_cents
    add_adjustment(conn, account, D(2026, 11, 2), diff, "couldn't find it")
    assert reconciliation_history(conn, account)[0].difference_cents == 0
    # Never spending, never in Review, never a transfer candidate.
    assert conn.execute("SELECT count(*) FROM spending_lines WHERE kind = 'adjustment'").fetchone()[0] == 0
    adj = conn.execute("SELECT id, note, merchant FROM transactions WHERE kind = 'adjustment'").fetchone()
    assert adj[1:] == ("couldn't find it", "Balance adjustment")
    with pytest.raises(ValueError, match="balance adjustment"):
        categorize_manually(conn, adj[0], basics["sub"])
    with pytest.raises(ValueError, match="balance adjustment"):
        mark_as_transfer(conn, adj[0])
    other = add(bank if which == "card" else basics["card"], -adj_amount(conn, adj[0]), "2026-11-02", kind=None,
              kind_source=None)
    assert all(c.id != adj[0] for c in candidates(conn, other, manual=True))


def adj_amount(conn, txn_id):
    return conn.execute("SELECT amount_cents FROM transactions WHERE id = %s", (txn_id,)).fetchone()[0]


def test_adjustment_rules(conn, bank):
    set_opening_balance(conn, bank, 0, D(2026, 10, 31))
    with pytest.raises(ValueError, match="no difference"):
        add_adjustment(conn, bank, D(2026, 11, 2), 0)
    with pytest.raises(ValueError, match="after the opening balance"):
        add_adjustment(conn, bank, D(2026, 10, 31), 100)


# ---- snapshots, opening balance, reconciled-through ----------------------------

def test_typed_snapshots_replace_and_delete(conn, bank):
    snapshot(conn, bank, D(2026, 11, 2), 100)
    snapshot(conn, bank, D(2026, 11, 2), 200)               # same day and source: replaced
    [r] = reconciliation_history(conn, bank)
    assert r.bank_cents == 200
    assert earliest_snapshot(conn, bank) == (D(2026, 11, 2), 200)
    delete_snapshot(conn, r.snapshot_id)
    assert reconciliation_history(conn, bank) == []
    with pytest.raises(ValueError):
        save_snapshot(conn, bank, D(2026, 11, 2), 1, "import")


def test_opening_balance_needs_amount_and_date(conn, bank):
    with pytest.raises(ValueError, match="both"):
        set_opening_balance(conn, bank, 100, None)
    set_opening_balance(conn, bank, None, None)


def test_reconciled_through_and_what_changes_it(conn, bank, basics):
    set_opening_balance(conn, bank, 10000, D(2026, 10, 31))
    cash_entry = add_manual(conn, txn_date=D(2026, 11, 1), amount_cents=1000, description="FAKE",
                            purchaser_id=basics["person"], account_id=bank)
    snapshot(conn, bank, D(2026, 11, 2), 9000)
    with pytest.raises(ValueError, match="matches"):
        set_reconciled_through(conn, bank, D(2026, 11, 3))       # no bank balance that day
    set_reconciled_through(conn, bank, D(2026, 11, 2))
    assert reconciled_through_if_affected(conn, bank, D(2026, 11, 1)) == D(2026, 11, 2)
    assert reconciled_through_if_affected(conn, bank, D(2026, 11, 3)) is None

    [summary] = [s for s in account_summaries(conn, D(2026, 11, 30)) if s.id == bank]
    assert (summary.status, summary.reconciled_difference_cents) == ("✓ Matches the bank", 0)
    delete_manual(conn, cash_entry)
    [summary] = [s for s in account_summaries(conn, D(2026, 11, 30)) if s.id == bank]
    assert summary.status == "⚠ Changed since reconciled"
    assert summary.reconciled_difference_cents == -1000


def test_reconciled_rows_in_batch(conn, basics, add):
    card = basics["card"]
    add(card, 100, "2026-11-01")
    add(card, 100, "2026-11-10")
    conn.execute("UPDATE accounts SET reconciled_through = '2026-11-05' WHERE id = %s", (card,))
    assert reconciled_rows_in_batch(conn, basics["batch"]) == (1, D(2026, 11, 5))


def test_summary_status_labels(conn, bank, add):
    def status():
        return next(s for s in account_summaries(conn, D(2026, 11, 30)) if s.id == bank).status
    assert status() == "No bank balance yet"
    set_opening_balance(conn, bank, 10000, D(2026, 10, 31))
    snapshot(conn, bank, D(2026, 11, 2), 9000)
    assert status() == "⚠ Off by $10.00"
    add(bank, 1000, "2026-11-01")
    assert status() == "✓ Matches the bank"


# ---- adding and editing accounts ----------------------------------------------

def test_add_and_edit_account(conn, basics, bank, add):
    new = save_account(conn, display_name=" Savings ", institution="Fake", account_type="savings",
                       owner_person_id=basics["person"], parser_key="bcu")
    row = conn.execute("SELECT display_name, account_class, account_type, parser_key FROM accounts WHERE id = %s",
                       (new,)).fetchone()
    assert row == ("Savings", "asset", "savings", "bcu")
    with pytest.raises(ValueError, match="already an account"):
        save_account(conn, display_name="Bank", institution="X", account_type="checking",
                     owner_person_id=basics["person"])
    # The connection is still usable after that error.
    add(bank, 100, "2026-11-01")
    with pytest.raises(ValueError, match="can't switch"):
        save_account(conn, account_id=bank, display_name="Bank", institution="Fake", account_type="credit_card",
                     owner_person_id=basics["person"])
    save_account(conn, account_id=bank, display_name="Bank 2", institution="Fake", account_type="savings",
                 owner_person_id=basics["person"], active=False)
    assert conn.execute("SELECT display_name, active FROM accounts WHERE id = %s", (bank,)).fetchone() == \
        ("Bank 2", False)


def test_rows_before_opening_are_reported(conn, bank, add):
    from budget.balances import rows_before_opening
    set_opening_balance(conn, bank, 10000, D(2026, 11, 1))
    add(bank, -500, "2026-10-31")       # e.g. interest dated before the opening balance
    add(bank, -500, "2026-11-01")       # on the opening day: also already included
    add(bank, -500, "2026-11-02")       # counted
    assert rows_before_opening(conn, bank) == (2, D(2026, 11, 1))
    assert balance_on(conn, bank, D(2026, 11, 30)) == 10500
