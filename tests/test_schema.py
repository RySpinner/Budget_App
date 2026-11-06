"""Each test checks one database-level guard from migrations/0001_core_schema.sql."""

import psycopg
import pytest
from psycopg import errors


def expect_error(conn, sql, args=(), error=psycopg.Error):
    """Run sql inside a savepoint and assert it fails."""
    with pytest.raises(error):
        with conn.transaction():
            conn.execute(sql, args)


# ---- effective_date / dates ------------------------------------------------

def test_effective_date_prefers_txn_date(conn, add_card_txn):
    tid = add_card_txn(txn_date="2026-01-31", posted_date="2026-02-02")
    assert str(conn.execute("SELECT effective_date FROM transactions WHERE id=%s", (tid,)).fetchone()[0]) == "2026-01-31"


def test_effective_date_falls_back_to_posted_date(conn, add_card_txn):
    tid = add_card_txn(txn_date=None, posted_date="2026-02-02")
    assert str(conn.execute("SELECT effective_date FROM transactions WHERE id=%s", (tid,)).fetchone()[0]) == "2026-02-02"


def test_transaction_needs_some_date(conn, add_card_txn):
    with pytest.raises(errors.CheckViolation):
        with conn.transaction():
            add_card_txn(txn_date=None, posted_date=None)


# ---- dedup -------------------------------------------------------------------

def test_fingerprint_is_unique(conn, add_card_txn):
    add_card_txn(fingerprint="same")
    with pytest.raises(errors.UniqueViolation):
        with conn.transaction():
            add_card_txn(fingerprint="same")


def test_on_conflict_do_nothing_skips_duplicates(conn, basics):
    sql = (
        "INSERT INTO transactions (source, account_id, import_batch_id, txn_date, amount_cents, "
        "raw_description, purchaser_id, fingerprint, kind, kind_source) VALUES ('import', %s, %s, '2026-01-01', 500, "
        "'FAKE COFFEE', %s, 'dup-fp', 'expense', 'parser') ON CONFLICT (fingerprint) DO NOTHING"
    )
    args = (basics["card"], basics["batch"], basics["person"])
    assert conn.execute(sql, args).rowcount == 1
    assert conn.execute(sql, args).rowcount == 0


# ---- imported vs manual -----------------------------------------------------

def test_manual_entry_needs_an_account_but_no_import_fields(conn, basics):
    conn.execute(
        "INSERT INTO transactions (source, account_id, txn_date, amount_cents, raw_description, purchaser_id) "
        "VALUES ('manual', %s, '2026-01-10', 2000, 'Cash: farmers market', %s)",
        (basics["checking"], basics["person"]),
    )
    expect_error(
        conn,
        "INSERT INTO transactions (source, txn_date, amount_cents, raw_description, purchaser_id) "
        "VALUES ('manual', '2026-01-10', 2000, 'no account', %s)",
        (basics["person"],),
        errors.NotNullViolation,
    )


def test_manual_entry_cannot_have_import_fields(conn, basics):
    expect_error(
        conn,
        "INSERT INTO transactions (source, account_id, import_batch_id, txn_date, amount_cents, raw_description, purchaser_id) "
        "VALUES ('manual', %s, %s, '2026-01-10', 2000, 'x', %s)",
        (basics["card"], basics["batch"], basics["person"]),
        errors.CheckViolation,
    )


def test_card_row_needs_fingerprint_and_batch(conn, add_card_txn):
    with pytest.raises(errors.CheckViolation):
        with conn.transaction():
            add_card_txn(fingerprint=None)
    with pytest.raises(errors.CheckViolation):
        with conn.transaction():
            add_card_txn(import_batch_id=None)


# ---- categories ------------------------------------------------------------

def test_categories_only_two_levels(conn, basics):
    expect_error(conn, "INSERT INTO categories (name, parent_id) VALUES ('Too deep', %s)", (basics["sub"],))


def test_category_with_children_cannot_become_subcategory(conn, basics):
    other_top = conn.execute("INSERT INTO categories (name) VALUES ('Other Top') RETURNING id").fetchone()[0]
    expect_error(conn, "UPDATE categories SET parent_id = %s WHERE id = %s", (other_top, basics["top"]))


def test_duplicate_top_level_names_rejected(conn, basics):
    expect_error(conn, "INSERT INTO categories (name) VALUES ('Test Top')", (), errors.UniqueViolation)


def test_transaction_must_use_subcategory(conn, add_card_txn, basics):
    with pytest.raises(psycopg.Error):
        with conn.transaction():
            add_card_txn(category_id=basics["top"], category_source="manual")
    add_card_txn(category_id=basics["sub"], category_source="manual")


def test_category_and_source_go_together(conn, add_card_txn, basics):
    with pytest.raises(errors.CheckViolation):
        with conn.transaction():
            add_card_txn(category_id=basics["sub"])  # no category_source
    with pytest.raises(errors.CheckViolation):
        with conn.transaction():
            add_card_txn(category_source="rule")  # no category_id


# ---- manual categories are protected from rules ----------------------------

def test_rule_cannot_overwrite_manual_category(conn, add_card_txn, basics):
    tid = add_card_txn(category_id=basics["sub"], category_source="manual")
    expect_error(
        conn,
        "UPDATE transactions SET category_id = %s, category_source = 'rule' WHERE id = %s",
        (basics["sub2"], tid),
    )
    # A person can still change their own manual choice.
    conn.execute("UPDATE transactions SET category_id = %s WHERE id = %s", (basics["sub2"], tid))


def test_rule_can_update_rule_category(conn, add_card_txn, basics):
    tid = add_card_txn(category_id=basics["sub"], category_source="rule")
    conn.execute("UPDATE transactions SET category_id = %s WHERE id = %s", (basics["sub2"], tid))


# ---- splits ----------------------------------------------------------------

def test_splits_must_sum_to_parent(conn, add_card_txn, basics):
    tid = add_card_txn(amount_cents=1000)
    with pytest.raises(errors.RaiseException):
        with conn.transaction():
            conn.execute("INSERT INTO splits (transaction_id, amount_cents, category_id) VALUES (%s, 600, %s)", (tid, basics["sub"]))
            conn.execute("INSERT INTO splits (transaction_id, amount_cents, category_id) VALUES (%s, 300, %s)", (tid, basics["sub2"]))
            conn.execute("SET CONSTRAINTS ALL IMMEDIATE")  # what COMMIT would do


def test_splits_that_sum_correctly_are_accepted(conn, add_card_txn, basics):
    tid = add_card_txn(amount_cents=1000)
    conn.execute("INSERT INTO splits (transaction_id, amount_cents, category_id) VALUES (%s, 600, %s)", (tid, basics["sub"]))
    conn.execute("INSERT INTO splits (transaction_id, amount_cents, category_id) VALUES (%s, 400, %s)", (tid, basics["sub2"]))
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_changing_parent_amount_breaks_splits(conn, add_card_txn, basics):
    tid = add_card_txn(amount_cents=1000)
    conn.execute("INSERT INTO splits (transaction_id, amount_cents, category_id) VALUES (%s, 1000, %s)", (tid, basics["sub"]))
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    with pytest.raises(errors.RaiseException):
        with conn.transaction():
            conn.execute("UPDATE transactions SET amount_cents = 999 WHERE id = %s", (tid,))
            conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_deleting_transaction_removes_splits(conn, add_card_txn, basics):
    tid = add_card_txn(amount_cents=1000)
    conn.execute("INSERT INTO splits (transaction_id, amount_cents, category_id) VALUES (%s, 1000, %s)", (tid, basics["sub"]))
    conn.execute("DELETE FROM transactions WHERE id = %s", (tid,))
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert conn.execute("SELECT count(*) FROM splits WHERE transaction_id = %s", (tid,)).fetchone()[0] == 0


# ---- budgets & rules -------------------------------------------------------

def test_budget_on_top_level_from_first_of_month(conn, basics):
    conn.execute("INSERT INTO budgets (category_id, monthly_amount_cents, effective_from) VALUES (%s, 50000, '2026-01-01')", (basics["top"],))
    expect_error(conn, "INSERT INTO budgets (category_id, monthly_amount_cents, effective_from) VALUES (%s, 50000, '2026-02-15')", (basics["top"],), errors.CheckViolation)
    expect_error(conn, "INSERT INTO budgets (category_id, monthly_amount_cents, effective_from) VALUES (%s, 50000, '2026-03-01')", (basics["sub"],))


def test_rule_validation(conn, basics):
    conn.execute("INSERT INTO rules (match_type, pattern, category_id) VALUES ('contains', 'SHELL', %s)", (basics["sub"],))
    expect_error(conn, "INSERT INTO rules (match_type, pattern, category_id) VALUES ('fuzzy', 'X', %s)", (basics["sub"],), errors.CheckViolation)
    expect_error(conn, "INSERT INTO rules (match_type, pattern, category_id, min_amount_cents, max_amount_cents) VALUES ('contains', 'X', %s, 500, 100)", (basics["sub"],), errors.CheckViolation)
    expect_error(conn, "INSERT INTO rules (match_type, pattern, category_id) VALUES ('contains', 'X', %s)", (basics["top"],))


def test_updated_at_changes_on_update(conn, add_card_txn):
    tid = add_card_txn()
    conn.execute("UPDATE transactions SET updated_at = '2000-01-01' WHERE id = %s", (tid,))
    conn.execute("UPDATE transactions SET note = 'hello' WHERE id = %s", (tid,))
    year = conn.execute("SELECT extract(year FROM updated_at) FROM transactions WHERE id = %s", (tid,)).fetchone()[0]
    assert year > 2000
