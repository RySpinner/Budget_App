"""Rules that mark rows as transfers between our own accounts (phase 9, part 2)."""

from pathlib import Path

import psycopg
import pytest

from budget.importer import commit_import, preview_import
from budget.rules import apply_rules, create_rule, load_rules, mark_transfer_manually


def row(conn, tid):
    return conn.execute(
        "SELECT kind, kind_source, category_id, category_source, rule_id FROM transactions WHERE id = %s", (tid,)
    ).fetchone()


@pytest.fixture
def unknown(add_card_txn, basics):
    """A checking row nobody has classified yet."""
    def _add(description="Withdrawal FAKE SAVINGS XFER", amount_cents=50000, **extra):
        cols = {"kind": None, "kind_source": None, "account_id": basics["checking"]} | extra
        return add_card_txn(raw_description=description, amount_cents=amount_cents, **cols)
    return _add


# ---- creating rules -------------------------------------------------------------

def test_transfer_rule_loads_with_counterpart(conn, basics):
    rid = create_rule(conn, "savings xfer", None, is_transfer=True, counterpart_account_id=basics["card"])
    r = next(r for r in load_rules(conn) if r.id == rid)
    assert (r.kind, r.is_transfer, r.category_id, r.counterpart_account_id) == ("transfer", True, None, basics["card"])


def test_rule_sets_a_category_or_transfer_not_both(conn, basics):
    with pytest.raises(ValueError, match="either"):
        create_rule(conn, "x", basics["sub"], is_transfer=True)
    with pytest.raises(ValueError, match="either"):
        create_rule(conn, "x", None)


def test_counterpart_cannot_be_the_rules_own_account(conn, basics):
    with pytest.raises(ValueError, match="other account"):
        create_rule(conn, "x", None, account_id=basics["checking"], is_transfer=True,
                    counterpart_account_id=basics["checking"])


def test_database_guards_rule_shape(conn, basics):
    for sql, args in [
        ("INSERT INTO rules (match_type, pattern, category_id, is_transfer) VALUES ('contains', 'x', %s, true)",
         (basics["sub"],)),
        ("INSERT INTO rules (match_type, pattern) VALUES ('contains', 'x')", ()),
        ("INSERT INTO rules (match_type, pattern, category_id, counterpart_account_id) "
         "VALUES ('contains', 'x', %s, %s)", (basics["sub"], basics["card"])),
        ("INSERT INTO rules (match_type, pattern, is_transfer, account_id, counterpart_account_id) "
         "VALUES ('contains', 'x', true, %s, %s)", (basics["card"], basics["card"])),
    ]:
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                conn.execute(sql, args)


# ---- applying rules -------------------------------------------------------------

def test_transfer_rule_marks_unknown_row(conn, basics, unknown):
    tid = unknown()
    rid = create_rule(conn, "savings xfer", None, is_transfer=True)
    assert apply_rules(conn).categorized == 1
    assert row(conn, tid) == ("transfer", "rule", None, None, rid)
    # A transfer is never spending, and it leaves the Review queue (no category needed).
    assert conn.execute("SELECT count(*) FROM spending_lines WHERE transaction_id = %s", (tid,)).fetchone()[0] == 0


def test_transfer_rule_released_goes_back_to_unknown(conn, basics, unknown):
    tid = unknown()
    rid = create_rule(conn, "savings xfer", None, is_transfer=True)
    apply_rules(conn)
    conn.execute("UPDATE rules SET active = false WHERE id = %s", (rid,))
    assert apply_rules(conn).uncategorized == 1
    assert row(conn, tid) == (None, None, None, None, None)


def test_deleted_rule_also_releases(conn, basics, unknown):
    tid = unknown()
    rid = create_rule(conn, "savings xfer", None, is_transfer=True)
    apply_rules(conn)
    conn.execute("DELETE FROM rules WHERE id = %s", (rid,))      # rule_id becomes NULL on the row
    assert apply_rules(conn).uncategorized == 1
    assert row(conn, tid) == (None, None, None, None, None)


def test_transfer_rule_on_a_card_purchase_then_released_back_to_expense(conn, add_card_txn, basics):
    tid = add_card_txn(raw_description="FAKE WALLET TOP-UP")           # card: parser said expense
    rid = create_rule(conn, "wallet top-up", None, is_transfer=True)
    apply_rules(conn)
    assert row(conn, tid) == ("transfer", "rule", None, None, rid)
    conn.execute("UPDATE rules SET active = false WHERE id = %s", (rid,))
    apply_rules(conn)
    assert row(conn, tid) == ("expense", "parser", None, None, None)   # the card default


def test_transfer_rule_adds_counterpart_to_parser_transfer(conn, add_card_txn, basics):
    tid = add_card_txn(raw_description="PAYMENT - THANK YOU", amount_cents=-50000, kind="transfer")
    rid = create_rule(conn, "payment - thank you", None, is_transfer=True, counterpart_account_id=basics["checking"])
    apply_rules(conn)
    assert row(conn, tid) == ("transfer", "parser", None, None, rid)   # still the parser's call; the rule adds the hint


def test_category_rule_still_skips_parser_transfers(conn, add_card_txn, basics):
    tid = add_card_txn(raw_description="PAYMENT - THANK YOU", amount_cents=-50000, kind="transfer")
    create_rule(conn, "payment", basics["sub"])
    apply_rules(conn)
    assert row(conn, tid) == ("transfer", "parser", None, None, None)


def test_category_rule_can_replace_a_transfer_rule(conn, basics, unknown):
    tid = unknown("Withdrawal FAKE GYM")
    create_rule(conn, "fake gym", None, is_transfer=True, priority=50)
    apply_rules(conn)
    assert row(conn, tid)[0] == "transfer"
    winner = create_rule(conn, "fake gym", basics["sub"], priority=10)
    assert apply_rules(conn).changed == 1
    assert row(conn, tid) == ("expense", "rule", basics["sub"], "rule", winner)


def test_transfer_rule_never_changes_a_manual_kind(conn, basics, unknown):
    tid = unknown(kind="expense", kind_source="manual")
    create_rule(conn, "savings xfer", None, is_transfer=True)
    apply_rules(conn)
    assert row(conn, tid) == ("expense", "manual", None, None, None)


def test_transfer_rule_on_a_hand_marked_transfer_keeps_it_manual(conn, basics, unknown):
    tid = unknown(kind="transfer", kind_source="manual")
    rid = create_rule(conn, "savings xfer", None, is_transfer=True)
    apply_rules(conn)
    assert row(conn, tid) == ("transfer", "manual", None, None, rid)


def test_rules_skip_linked_transfers(conn, add_card_txn, basics):
    a = add_card_txn(raw_description="FAKE MOVE", amount_cents=500, kind="transfer", kind_source="manual",
                     account_id=basics["checking"])
    b = add_card_txn(raw_description="FAKE MOVE", amount_cents=-500, kind="transfer", kind_source="manual")
    t = conn.execute("INSERT INTO transfers (matched_by) VALUES ('manual') RETURNING id").fetchone()[0]
    conn.execute("UPDATE transactions SET transfer_id = %s WHERE id IN (%s, %s)", (t, a, b))
    create_rule(conn, "fake move", None, is_transfer=True)
    apply_rules(conn)
    assert row(conn, a)[4] is None and row(conn, b)[4] is None


def test_account_limited_transfer_rule(conn, add_card_txn, basics, unknown):
    in_checking = unknown("FAKE SHARED WORDS")
    on_card = add_card_txn(raw_description="FAKE SHARED WORDS")
    create_rule(conn, "fake shared words", None, account_id=basics["checking"], is_transfer=True)
    apply_rules(conn)
    assert row(conn, in_checking)[0] == "transfer"
    assert row(conn, on_card)[:2] == ("expense", "parser")


# ---- from the Review page ----------------------------------------------------------

def test_mark_transfer_manually_with_rule_sorts_similar_rows_in_that_account(conn, add_card_txn, basics, unknown):
    first = unknown("Withdrawal FAKE SAVINGS XFER 1")
    second = unknown("Withdrawal FAKE SAVINGS XFER 2")
    on_card = add_card_txn(raw_description="FAKE SAVINGS XFER", kind=None, kind_source=None, account_id=basics["card"])
    rid, others = mark_transfer_manually(conn, first, "FAKE SAVINGS XFER",
                                         counterpart_account_id=basics["card"], rule_account_id=basics["checking"])
    assert others == 1
    assert row(conn, first) == ("transfer", "manual", None, None, None)
    assert row(conn, second) == ("transfer", "rule", None, None, rid)
    assert row(conn, on_card)[0] is None        # other account: untouched
    assert conn.execute("SELECT account_id, counterpart_account_id, created_from_transaction_id FROM rules "
                        "WHERE id = %s", (rid,)).fetchone() == (basics["checking"], basics["card"], first)


def test_mark_transfer_manually_without_rule(conn, basics, unknown):
    tid = unknown()
    assert mark_transfer_manually(conn, tid) == (None, 0)
    assert conn.execute("SELECT count(*) FROM rules").fetchone()[0] == 0


# ---- on import ------------------------------------------------------------------------

def test_transfer_rule_applies_on_import(conn, basics, tmp_path):
    conn.execute("UPDATE accounts SET parser_key = 'bcu' WHERE id = %s", (basics["checking"],))
    rid = create_rule(conn, "transfer from savings", None, account_id=basics["checking"], is_transfer=True,
                      counterpart_account_id=basics["card"])
    data = (Path(__file__).parent / "fixtures" / "bcu" / "checking.csv").read_bytes()
    p = preview_import(conn, basics["checking"], "checking.csv", data)
    savings = next(n for n in p.new_rows if "SAVINGS" in n.parsed.raw_description)
    assert (savings.kind, savings.kind_source, savings.rule.id) == ("transfer", "rule", rid)
    assert p.transfer_count == 2           # this one and the card payment
    assert p.unknown_kind_count == 3
    batch = commit_import(conn, p, archive=tmp_path)
    stored = conn.execute("SELECT kind, kind_source, category_id, category_source, rule_id FROM transactions "
                          "WHERE import_batch_id = %s AND raw_description LIKE '%%SAVINGS%%'", (batch,)).fetchone()
    assert stored == ("transfer", "rule", None, None, rid)
