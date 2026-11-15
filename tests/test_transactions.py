from datetime import date

import pytest
from psycopg import errors

from budget.transactions import (
    UNCATEGORIZED, Filters, cash_account_id, SplitLine, add_manual, delete_manual, get_splits, load_lines,
    months_with_data, set_splits, update_line,
)


# ---- spending_lines view ---------------------------------------------------

def test_unsplit_transaction_is_one_line(conn, add_card_txn, basics):
    add_card_txn(amount_cents=1000, category_id=basics["sub"], category_source="manual")
    lines = load_lines(conn, Filters())
    assert [(l.amount_cents, l.category_id, l.top_category_id, l.split_id) for l in lines] == [
        (1000, basics["sub"], basics["top"], None)
    ]


def test_split_transaction_becomes_its_split_lines(conn, add_card_txn, basics):
    tid = add_card_txn(amount_cents=1000)
    set_splits(conn, tid, [SplitLine(600, basics["sub"], "food"), SplitLine(400, basics["sub2"])])
    lines = load_lines(conn, Filters())
    assert sorted((l.amount_cents, l.category_id, l.note, l.split_count) for l in lines) == [
        (400, basics["sub2"], None, 2), (600, basics["sub"], "food", 2)
    ]


def test_filters(conn, add_card_txn, basics):
    add_card_txn(amount_cents=100, txn_date="2026-01-05", category_id=basics["sub"], category_source="manual")
    add_card_txn(amount_cents=200, txn_date="2026-02-05")
    manual = add_manual(conn, txn_date=date(2026, 2, 6), amount_cents=300, description="Cash thing",
                        purchaser_id=basics["person"])

    amounts = lambda f: sorted(l.amount_cents for l in load_lines(conn, f))  # noqa: E731
    assert amounts(Filters(month=date(2026, 2, 1))) == [200, 300]
    assert amounts(Filters(top_category_id=basics["top"])) == [100]
    assert amounts(Filters(top_category_id=UNCATEGORIZED)) == [200, 300]
    assert amounts(Filters(category_id=basics["sub"])) == [100]
    assert amounts(Filters(account_id=cash_account_id(conn))) == [300]
    assert amounts(Filters(account_id=basics["card"])) == [100, 200]
    assert amounts(Filters(purchaser_id=basics["person"])) == [100, 200, 300]
    assert months_with_data(conn) == [date(2026, 2, 1), date(2026, 1, 1)]
    assert manual


def test_running_total_resets_each_month(conn, add_card_txn):
    for d, amt in [("2026-01-30", 100), ("2026-01-10", 50), ("2026-02-01", 7), ("2026-02-02", -2)]:
        add_card_txn(amount_cents=amt, txn_date=d)
    lines = load_lines(conn, Filters())
    assert [(str(l.effective_date), l.running_total_cents) for l in lines] == [
        ("2026-01-10", 50), ("2026-01-30", 150), ("2026-02-01", 7), ("2026-02-02", 5),
    ]


# ---- budget functions -------------------------------------------------------

def test_budget_for_month_uses_latest_effective_row(conn, basics):
    conn.execute("INSERT INTO budgets (category_id, monthly_amount_cents, effective_from) VALUES (%s, 30000, '2026-01-01')", (basics["top"],))
    conn.execute("INSERT INTO budgets (category_id, monthly_amount_cents, effective_from) VALUES (%s, 45000, '2026-03-01')", (basics["top"],))
    q = lambda d: conn.execute("SELECT budget_for_month(%s, %s)", (basics["top"], d)).fetchone()[0]  # noqa: E731
    assert q("2025-12-31") is None
    assert q("2026-02-28") == 30000
    assert q("2026-03-01") == 45000
    assert q("2027-01-15") == 45000


def test_budget_to_date_prorates_exactly(conn, basics):
    conn.execute("INSERT INTO budgets (category_id, monthly_amount_cents, effective_from) VALUES (%s, 31000, '2026-01-01')", (basics["top"],))
    q = lambda d: conn.execute("SELECT budget_to_date(%s, %s)", (basics["top"], d)).fetchone()[0]  # noqa: E731
    assert q("2026-01-01") == 1000      # 310.00 * 1/31
    assert q("2026-01-31") == 31000
    assert q("2026-02-14") == 15500     # 310.00 * 14/28


def test_lines_carry_budget_to_date_in_category_view(conn, add_card_txn, basics):
    conn.execute("INSERT INTO budgets (category_id, monthly_amount_cents, effective_from) VALUES (%s, 31000, '2026-01-01')", (basics["top"],))
    add_card_txn(txn_date="2026-01-10", category_id=basics["sub"], category_source="manual")
    [line] = load_lines(conn, Filters(top_category_id=basics["top"]))
    assert line.budget_to_date_cents == 10000
    [line] = load_lines(conn, Filters())
    assert line.budget_to_date_cents is None


# ---- editing ------------------------------------------------------------------

def test_update_line_sets_manual_category_note_and_purchaser(conn, add_card_txn, basics):
    other = conn.execute("INSERT INTO people (name) VALUES ('Other Person') RETURNING id").fetchone()[0]
    tid = add_card_txn(category_id=basics["sub"], category_source="rule")
    update_line(conn, tid, None, category_id=basics["sub2"], note="  gift  ", purchaser_id=other)
    assert conn.execute("SELECT category_id, category_source, note, purchaser_id FROM transactions WHERE id = %s", (tid,)).fetchone() == (
        basics["sub2"], "manual", "gift", other)


def test_update_line_unchanged_category_keeps_rule_source(conn, add_card_txn, basics):
    tid = add_card_txn(category_id=basics["sub"], category_source="rule")
    update_line(conn, tid, None, category_id=basics["sub"], note="just a note", purchaser_id=basics["person"])
    assert conn.execute("SELECT category_source FROM transactions WHERE id = %s", (tid,)).fetchone()[0] == "rule"


def test_update_line_can_clear_category(conn, add_card_txn, basics):
    tid = add_card_txn(category_id=basics["sub"], category_source="manual")
    update_line(conn, tid, None, category_id=None, note=None, purchaser_id=basics["person"])
    assert conn.execute("SELECT category_id, category_source FROM transactions WHERE id = %s", (tid,)).fetchone() == (None, None)


def test_update_split_line(conn, add_card_txn, basics):
    tid = add_card_txn(amount_cents=1000)
    set_splits(conn, tid, [SplitLine(600, basics["sub"]), SplitLine(400, basics["sub"])])
    split_id = conn.execute("SELECT min(id) FROM splits WHERE transaction_id = %s", (tid,)).fetchone()[0]
    update_line(conn, tid, split_id, category_id=basics["sub2"], note="n", purchaser_id=basics["person"])
    assert conn.execute("SELECT category_id, note FROM splits WHERE id = %s", (split_id,)).fetchone() == (basics["sub2"], "n")
    with pytest.raises(ValueError):
        update_line(conn, tid, split_id, category_id=None, note=None, purchaser_id=basics["person"])


def test_set_splits_validation(conn, add_card_txn, basics):
    tid = add_card_txn(amount_cents=1000)
    with pytest.raises(ValueError, match="add up"):
        set_splits(conn, tid, [SplitLine(600, basics["sub"]), SplitLine(300, basics["sub"])])
    with pytest.raises(ValueError, match="at least two"):
        set_splits(conn, tid, [SplitLine(1000, basics["sub"])])
    assert get_splits(conn, tid) == []


def test_replace_and_remove_splits(conn, add_card_txn, basics):
    tid = add_card_txn(amount_cents=1000)
    set_splits(conn, tid, [SplitLine(500, basics["sub"]), SplitLine(500, basics["sub2"])])
    set_splits(conn, tid, [SplitLine(100, basics["sub"]), SplitLine(200, basics["sub"]), SplitLine(700, basics["sub2"])])
    assert [s.amount_cents for s in get_splits(conn, tid)] == [100, 200, 700]
    set_splits(conn, tid, [])
    assert get_splits(conn, tid) == []
    assert len(load_lines(conn, Filters())) == 1


def test_negative_refund_split(conn, add_card_txn, basics):
    tid = add_card_txn(amount_cents=-1500)
    set_splits(conn, tid, [SplitLine(-1000, basics["sub"]), SplitLine(-500, basics["sub2"])])
    assert sum(s.amount_cents for s in get_splits(conn, tid)) == -1500


# ---- manual entries -----------------------------------------------------------

def test_add_and_delete_manual(conn, basics):
    tid = add_manual(conn, txn_date=date(2026, 3, 1), amount_cents=2500, description=" Farmers market ",
                     purchaser_id=basics["person"], category_id=basics["sub"], note="veg")
    row = conn.execute(
        "SELECT source, account_id, posted_date, raw_description, merchant, category_source, note FROM transactions WHERE id = %s",
        (tid,),
    ).fetchone()
    assert row == ("manual", cash_account_id(conn), None, "Farmers market", "Farmers market", "manual", "veg")
    delete_manual(conn, tid)
    assert conn.execute("SELECT count(*) FROM transactions WHERE id = %s", (tid,)).fetchone()[0] == 0


def test_manual_entry_validation(conn, basics):
    with pytest.raises(ValueError):
        add_manual(conn, txn_date=date(2026, 3, 1), amount_cents=0, description="x", purchaser_id=basics["person"])
    with pytest.raises(ValueError):
        add_manual(conn, txn_date=date(2026, 3, 1), amount_cents=5, description="  ", purchaser_id=basics["person"])


def test_cannot_delete_card_row_as_manual(conn, add_card_txn):
    tid = add_card_txn()
    with pytest.raises(ValueError, match="undo the import"):
        delete_manual(conn, tid)


def test_split_sum_still_enforced_by_database(conn, add_card_txn, basics):
    tid = add_card_txn(amount_cents=1000)
    with pytest.raises(errors.RaiseException):
        with conn.transaction():
            conn.execute("INSERT INTO splits (transaction_id, amount_cents, category_id) VALUES (%s, 1, %s)", (tid, basics["sub"]))
            conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


# ---- kinds (v2) ------------------------------------------------------------------

def test_kind_filter(conn, add_card_txn, basics):
    add_card_txn(amount_cents=100)
    add_card_txn(amount_cents=-500, kind="transfer")
    add_card_txn(amount_cents=-900, kind=None, kind_source=None, account_id=basics["checking"])
    kinds = lambda k: [l.amount_cents for l in load_lines(conn, Filters(kind=k))]  # noqa: E731
    assert kinds("expense") == [100]
    assert kinds("transfer") == [-500]
    assert kinds("unknown") == [-900]
    assert sorted(kinds(None)) == [-900, -500, 100]


def test_mark_as_transfer_and_back(conn, add_card_txn, basics):
    from budget.transactions import TRANSFER
    tid = add_card_txn(amount_cents=-50000, category_id=basics["sub"], category_source="rule")
    update_line(conn, tid, None, category_id=TRANSFER, note=None, purchaser_id=basics["person"])
    assert conn.execute("SELECT kind, kind_source, category_id FROM transactions WHERE id = %s",
                        (tid,)).fetchone() == ("transfer", "manual", None)
    update_line(conn, tid, None, category_id=basics["income"], note=None, purchaser_id=basics["person"])
    assert conn.execute("SELECT kind, category_id FROM transactions WHERE id = %s",
                        (tid,)).fetchone() == ("income", basics["income"])


def test_recategorizing_a_linked_transfer_unlinks_both_legs(conn, add_card_txn, basics):
    out = add_card_txn(amount_cents=500, kind="transfer", account_id=basics["checking"])
    into = add_card_txn(amount_cents=-500, kind="transfer")
    t = conn.execute("INSERT INTO transfers (matched_by) VALUES ('manual') RETURNING id").fetchone()[0]
    conn.execute("UPDATE transactions SET transfer_id = %s WHERE id IN (%s, %s)", (t, out, into))
    update_line(conn, out, None, category_id=basics["sub"], note=None, purchaser_id=basics["person"])
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert conn.execute("SELECT kind, transfer_id FROM transactions WHERE id = %s", (out,)).fetchone() == ("expense", None)
    assert conn.execute("SELECT kind, transfer_id FROM transactions WHERE id = %s", (into,)).fetchone() == ("transfer", None)


def test_transfers_cannot_be_split(conn, add_card_txn, basics):
    tid = add_card_txn(amount_cents=1000, kind="transfer")
    with pytest.raises(ValueError, match="Only expense or income"):
        set_splits(conn, tid, [SplitLine(500, basics["sub"]), SplitLine(500, basics["sub"])])


def test_split_row_cannot_become_transfer(conn, add_card_txn, basics):
    from budget.transactions import mark_as_transfer
    tid = add_card_txn(amount_cents=1000)
    set_splits(conn, tid, [SplitLine(500, basics["sub"]), SplitLine(500, basics["sub2"])])
    with pytest.raises(ValueError, match="Remove the split"):
        mark_as_transfer(conn, tid)


def test_manual_income_entry(conn, basics):
    tid = add_manual(conn, txn_date=date(2026, 3, 1), amount_cents=-20000, description="Sold a bike",
                     purchaser_id=basics["person"], category_id=basics["income"], account_id=basics["checking"])
    assert conn.execute("SELECT kind, account_id FROM transactions WHERE id = %s", (tid,)).fetchone() == (
        "income", basics["checking"])



# ---- editing manual entries ----------------------------------------------------

def _savings(conn, basics):
    return conn.execute(
        "INSERT INTO accounts (display_name, institution, owner_person_id, account_class, account_type) "
        "VALUES ('Hand savings', 'Fake', %s, 'asset', 'savings') RETURNING id", (basics["person"],),
    ).fetchone()[0]


def test_update_manual_moves_entry_to_another_account(conn, basics):
    from datetime import date

    from budget.balances import balance_on
    from budget.transactions import add_manual, update_manual
    savings = _savings(conn, basics)
    cash = conn.execute("SELECT id FROM accounts WHERE account_type = 'cash'").fetchone()[0]
    tid = add_manual(conn, txn_date=date(2026, 11, 10), amount_cents=-500, description="Interst",
                     purchaser_id=basics["person"], category_id=basics["income"])    # oops: went into Cash
    assert balance_on(conn, savings, date(2026, 11, 30)) == 0
    notes = update_manual(conn, tid, txn_date=date(2026, 11, 9), amount_cents=-510, description=" Interest ",
                          account_id=savings)
    assert notes == []
    row = conn.execute("SELECT account_id, txn_date::text, amount_cents, raw_description, merchant, kind, "
                       "category_id FROM transactions WHERE id = %s", (tid,)).fetchone()
    assert row == (savings, "2026-11-09", -510, "Interest", "Interest", "income", basics["income"])
    assert balance_on(conn, savings, date(2026, 11, 30)) == 510
    assert balance_on(conn, cash, date(2026, 11, 30)) == 0


def test_update_manual_refusals(conn, basics, add_card_txn):
    from datetime import date

    import pytest

    from budget.balances import add_adjustment
    from budget.transactions import SplitLine, add_manual, set_splits, update_manual
    from budget.transfers import generate_leg
    savings = _savings(conn, basics)
    kw = dict(txn_date=date(2026, 11, 1), amount_cents=100, description="X", account_id=savings)
    with pytest.raises(ValueError, match="imported rows"):
        update_manual(conn, add_card_txn(), **kw)
    adj = add_adjustment(conn, savings, date(2026, 11, 2), 100)
    with pytest.raises(ValueError, match="Balance adjustments"):
        update_manual(conn, adj, **kw)
    leg = add_card_txn(account_id=basics["card"], amount_cents=-2000, kind="transfer")
    generated = generate_leg(conn, leg, savings)
    with pytest.raises(ValueError, match="other half of a transfer"):
        update_manual(conn, generated, **kw)
    split = add_manual(conn, txn_date=date(2026, 11, 1), amount_cents=3000, description="Shop",
                       purchaser_id=basics["person"], category_id=basics["sub"])
    set_splits(conn, split, [SplitLine(2000, basics["sub"]), SplitLine(1000, basics["sub2"])])
    with pytest.raises(ValueError, match="remove the split"):
        update_manual(conn, split, txn_date=date(2026, 11, 3), amount_cents=3100, description="Shop",
                      account_id=savings)
    # Date, description and account can still change on a split entry.
    update_manual(conn, split, txn_date=date(2026, 11, 3), amount_cents=3000, description="Shop 2",
                  account_id=savings)


def test_update_manual_transfer_leg(conn, basics, add_card_txn):
    from datetime import date

    from budget.transactions import add_manual, mark_as_transfer, update_manual
    from budget.transfers import generate_leg
    savings = _savings(conn, basics)
    # A hand-entered savings withdrawal linked to the card payment it paid for.
    card_leg = add_card_txn(account_id=basics["card"], amount_cents=-2000, kind="transfer",
                            txn_date="2026-11-05", posted_date=None)
    manual = add_manual(conn, txn_date=date(2026, 11, 5), amount_cents=2000, description="To card",
                        purchaser_id=basics["person"], account_id=savings)
    mark_as_transfer(conn, manual)                      # links automatically: one candidate each way
    assert conn.execute("SELECT transfer_id FROM transactions WHERE id = %s", (manual,)).fetchone()[0]

    # A date-only change keeps the link.
    assert update_manual(conn, manual, txn_date=date(2026, 11, 6), amount_cents=2000, description="To card",
                         account_id=savings) == []
    # A new amount can't cancel out the card's leg: the link is undone, and nothing matches now.
    notes = update_manual(conn, manual, txn_date=date(2026, 11, 6), amount_cents=2500, description="To card",
                          account_id=savings)
    assert "Link it again in Review" in notes[0]
    assert conn.execute("SELECT count(*) FROM transfers").fetchone()[0] == 0
    # Back to the right amount: linked to the card's leg again automatically.
    notes = update_manual(conn, manual, txn_date=date(2026, 11, 6), amount_cents=2000, description="To card",
                          account_id=savings)
    assert notes == ["It was linked to its transfer's other side automatically."]
    assert conn.execute("SELECT transfer_id FROM transactions WHERE id = %s", (card_leg,)).fetchone()[0] is not None

    # A generated leg follows its original's date.
    cash_out = add_manual(conn, txn_date=date(2026, 11, 7), amount_cents=4000, description="Moved to savings",
                          purchaser_id=basics["person"])
    mark_as_transfer(conn, cash_out)
    generated = generate_leg(conn, cash_out, savings)
    update_manual(conn, cash_out, txn_date=date(2026, 11, 8), amount_cents=4000, description="Moved to savings",
                  account_id=conn.execute("SELECT id FROM accounts WHERE account_type = 'cash'").fetchone()[0])
    assert conn.execute("SELECT txn_date::text FROM transactions WHERE id = %s", (generated,)).fetchone()[0] \
        == "2026-11-08"
