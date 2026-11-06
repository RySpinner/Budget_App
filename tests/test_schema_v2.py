"""Database guards added in migration 0004 (accounts, kinds, transfers)."""

import psycopg
import pytest
from psycopg import errors


def expect_error(conn, sql, args=(), error=psycopg.Error):
    with pytest.raises(error):
        with conn.transaction():
            conn.execute(sql, args)
            conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def new_transfer(conn, matched_by="manual"):
    return conn.execute("INSERT INTO transfers (matched_by) VALUES (%s) RETURNING id", (matched_by,)).fetchone()[0]


def link(conn, transfer_id, *txn_ids):
    for t in txn_ids:
        conn.execute("UPDATE transactions SET transfer_id = %s WHERE id = %s", (transfer_id, t))


# ---- accounts -------------------------------------------------------------------

def test_account_class_must_fit_type(conn, basics):
    expect_error(conn, "INSERT INTO accounts (display_name, institution, owner_person_id, account_class, account_type) "
                       "VALUES ('Bad', 'X', %s, 'asset', 'credit_card')", (basics["person"],), errors.CheckViolation)


def test_opening_balance_needs_both_parts(conn, basics):
    expect_error(conn, "UPDATE accounts SET opening_balance_cents = 100 WHERE id = %s", (basics["checking"],),
                 errors.CheckViolation)
    conn.execute("UPDATE accounts SET opening_balance_cents = 100, opening_balance_date = '2026-01-01' WHERE id = %s",
                 (basics["checking"],))


def test_joint_person_and_cash_account_exist(conn):
    assert conn.execute("SELECT count(*) FROM people WHERE name = 'Joint'").fetchone()[0] == 1
    assert conn.execute("SELECT account_class, parser_key FROM accounts WHERE account_type = 'cash'"
                        ).fetchone() == ("asset", None)


def test_balance_snapshot_unique_per_day_and_source(conn, basics):
    sql = ("INSERT INTO balance_snapshots (account_id, as_of_date, balance_cents, source) "
           "VALUES (%s, '2026-01-31', 123456, 'import')")
    conn.execute(sql, (basics["checking"],))
    expect_error(conn, sql, (basics["checking"],), errors.UniqueViolation)


# ---- kinds and categories -----------------------------------------------------------

def test_subcategory_inherits_kind_and_follows_parent(conn, basics):
    assert conn.execute("SELECT kind FROM categories WHERE id = %s", (basics["income"],)).fetchone()[0] == "income"
    child = conn.execute("INSERT INTO categories (name, parent_id, kind) VALUES ('Sneaky', %s, 'expense') RETURNING id",
                         (basics["income_top"],)).fetchone()[0]
    assert conn.execute("SELECT kind FROM categories WHERE id = %s", (child,)).fetchone()[0] == "income"
    conn.execute("UPDATE categories SET kind = 'expense' WHERE id = %s", (basics["income_top"],))
    assert conn.execute("SELECT kind FROM categories WHERE id = %s", (basics["income"],)).fetchone()[0] == "expense"


def test_category_kind_must_match_transaction_kind(conn, add_card_txn, basics):
    with pytest.raises(psycopg.Error):
        with conn.transaction():
            add_card_txn(category_id=basics["income"], category_source="manual")   # kind expense, income category
    add_card_txn(category_id=basics["income"], category_source="manual", kind="income")


def test_transfer_row_cannot_have_a_category(conn, add_card_txn, basics):
    with pytest.raises(psycopg.Error):
        with conn.transaction():
            add_card_txn(kind="transfer", category_id=basics["sub"], category_source="manual")


def test_kind_and_source_go_together(conn, add_card_txn):
    with pytest.raises(errors.CheckViolation):
        with conn.transaction():
            add_card_txn(kind="expense", kind_source=None)


def test_rule_cannot_overwrite_manual_kind(conn, add_card_txn):
    tid = add_card_txn(kind="expense", kind_source="manual")
    expect_error(conn, "UPDATE transactions SET kind = 'income', kind_source = 'rule' WHERE id = %s", (tid,))


def test_split_categories_must_match_kind(conn, add_card_txn, basics):
    tid = add_card_txn(amount_cents=1000)
    expect_error(conn, "INSERT INTO splits (transaction_id, amount_cents, category_id) VALUES (%s, 1000, %s)",
                 (tid, basics["income"]))


def test_budgets_only_on_expense_categories(conn, basics):
    expect_error(conn, "INSERT INTO budgets (category_id, monthly_amount_cents, effective_from) VALUES (%s, 100, '2026-01-01')",
                 (basics["income_top"],))


# ---- transfers ------------------------------------------------------------------------

def test_valid_transfer(conn, add_card_txn, basics):
    out = add_card_txn(amount_cents=50000, kind="transfer", account_id=basics["checking"])
    into = add_card_txn(amount_cents=-50000, kind="transfer")
    link(conn, new_transfer(conn), out, into)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_transfer_legs_must_cancel_out(conn, add_card_txn, basics):
    out = add_card_txn(amount_cents=50000, kind="transfer", account_id=basics["checking"])
    into = add_card_txn(amount_cents=-49999, kind="transfer")
    with pytest.raises(errors.RaiseException, match="cancel out"):
        with conn.transaction():
            link(conn, new_transfer(conn), out, into)
            conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_transfer_needs_two_different_accounts(conn, add_card_txn):
    a = add_card_txn(amount_cents=500, kind="transfer")
    b = add_card_txn(amount_cents=-500, kind="transfer")
    with pytest.raises(errors.RaiseException, match="same account"):
        with conn.transaction():
            link(conn, new_transfer(conn), a, b)
            conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_transfer_needs_exactly_two_legs(conn, add_card_txn):
    a = add_card_txn(amount_cents=500, kind="transfer")
    with pytest.raises(errors.RaiseException, match="exactly 2"):
        with conn.transaction():
            link(conn, new_transfer(conn), a)
            conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_only_transfer_rows_can_be_linked(conn, add_card_txn):
    a = add_card_txn(amount_cents=500)   # an expense
    expect_error(conn, "UPDATE transactions SET transfer_id = %s WHERE id = %s", (new_transfer(conn), a),
                 errors.CheckViolation)


def test_changing_a_leg_amount_breaks_the_transfer(conn, add_card_txn, basics):
    out = add_card_txn(amount_cents=500, kind="transfer", account_id=basics["checking"])
    into = add_card_txn(amount_cents=-500, kind="transfer")
    link(conn, new_transfer(conn), out, into)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    expect_error(conn, "UPDATE transactions SET amount_cents = 499 WHERE id = %s", (out,))


def test_deleting_a_leg_unmatches_the_other(conn, add_card_txn, basics):
    out = add_card_txn(amount_cents=500, kind="transfer", account_id=basics["checking"])
    into = add_card_txn(amount_cents=-500, kind="transfer")
    tid = new_transfer(conn)
    link(conn, tid, out, into)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    conn.execute("DELETE FROM transactions WHERE id = %s", (out,))
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert conn.execute("SELECT transfer_id FROM transactions WHERE id = %s", (into,)).fetchone()[0] is None
    assert conn.execute("SELECT count(*) FROM transfers WHERE id = %s", (tid,)).fetchone()[0] == 0


def test_transfers_and_income_never_in_spending(conn, add_card_txn, basics):
    add_card_txn(amount_cents=1000)                                                         # spending
    add_card_txn(amount_cents=-50000, kind="transfer")                                      # card payment
    add_card_txn(amount_cents=-300000, kind="income", category_id=basics["income"],
                 category_source="manual", account_id=basics["checking"])                   # paycheck
    assert conn.execute("SELECT count(*), sum(amount_cents) FROM spending_lines").fetchone() == (1, 1000)
    assert conn.execute("SELECT count(*) FROM transaction_lines").fetchone()[0] == 3
