import pytest

from budget.importer import commit_import, preview_import
from budget.rules import (
    Rule, apply_rules, categorize_manually, create_rule, first_match, suggest_pattern, validate_rule,
)


def rule(**kw):
    base = dict(id=1, priority=100, match_type="contains", pattern="coffee", card_id=None,
                min_amount_cents=None, max_amount_cents=None, category_id=1, merchant_name=None)
    return Rule(**(base | kw))


# ---- matching (no database) -------------------------------------------------

def test_contains_ignores_case_and_extra_spaces():
    assert rule(pattern="fake  coffee").matches("SQ *FAKE COFFEE ROASTERS", None, 450)


def test_exact():
    r = rule(match_type="exact", pattern="fake bookshop")
    assert r.matches("FAKE BOOKSHOP", None, 1)
    assert not r.matches("FAKE BOOKSHOP 123", None, 1)


def test_regex():
    r = rule(match_type="regex", pattern=r"^(SHELL|EXXON)\b")
    assert r.matches("shell oil 123", None, 1)
    assert not r.matches("SEASHELL SHOP", None, 1)


def test_card_and_amount_filters():
    r = rule(card_id=7, min_amount_cents=1000, max_amount_cents=5000)
    assert r.matches("coffee", 7, 1000)
    assert r.matches("coffee", 7, 5000)
    assert not r.matches("coffee", 8, 2000)
    assert not r.matches("coffee", 7, 999)
    assert not r.matches("coffee", 7, 5001)


def test_first_match_wins_in_given_order():
    rules = [rule(id=1, category_id=10), rule(id=2, category_id=20)]
    assert first_match(rules, "COFFEE", None, 1).id == 1
    assert first_match(rules, "TEA", None, 1) is None


def test_validate_rule():
    validate_rule("contains", "x")
    for match_type, pattern in [("fuzzy", "x"), ("contains", "  "), ("regex", "(")]:
        with pytest.raises(ValueError):
            validate_rule(match_type, pattern)


@pytest.mark.parametrize("merchant, raw, expected", [
    ("FAKE PIZZA SPRINGFIELD", "TST* FAKE PIZZA #0123 SPRINGFIELD IL", "FAKE PIZZA"),
    ("FAKE AUTO PARTS", "FAKE AUTO PARTS 5550001234 CT", "FAKE AUTO PARTS"),
    ("Something Else", "RAW  TEXT", "RAW TEXT"),
    (None, "RAW TEXT", "RAW TEXT"),
])
def test_suggest_pattern(merchant, raw, expected):
    assert suggest_pattern(merchant, raw) == expected


# ---- applying rules (database) ----------------------------------------------


def test_apply_rules_categorizes_uncategorized(conn, add_card_txn, basics):
    tid = add_card_txn(raw_description="FAKE COFFEE SHOP")
    other = add_card_txn(raw_description="FAKE TEA SHOP")
    rule_id = create_rule(conn, "coffee", basics["sub"], merchant_name="Fake Coffee")

    result = apply_rules(conn)
    assert result.categorized == 1
    row = conn.execute("SELECT category_id, category_source, rule_id, merchant FROM transactions WHERE id = %s", (tid,)).fetchone()
    assert row == (basics["sub"], "rule", rule_id, "Fake Coffee")
    assert conn.execute("SELECT category_id FROM transactions WHERE id = %s", (other,)).fetchone()[0] is None


def test_apply_rules_never_touches_manual(conn, add_card_txn, basics):
    tid = add_card_txn(raw_description="FAKE COFFEE SHOP", category_id=basics["sub2"], category_source="manual")
    create_rule(conn, "coffee", basics["sub"])
    result = apply_rules(conn)
    assert (result.categorized, result.changed) == (0, 0)
    assert conn.execute("SELECT category_id, category_source FROM transactions WHERE id = %s", (tid,)).fetchone() == (basics["sub2"], "manual")


def test_priority_then_age_decides(conn, add_card_txn, basics):
    tid = add_card_txn(raw_description="FAKE COFFEE SHOP")
    create_rule(conn, "shop", basics["sub2"], priority=100)
    winner = create_rule(conn, "coffee", basics["sub"], priority=10)
    apply_rules(conn)
    assert conn.execute("SELECT rule_id FROM transactions WHERE id = %s", (tid,)).fetchone()[0] == winner


def test_rerun_after_edit_moves_or_releases_rows(conn, add_card_txn, basics):
    tid = add_card_txn(raw_description="FAKE COFFEE SHOP")
    rule_id = create_rule(conn, "coffee", basics["sub"])
    apply_rules(conn)

    conn.execute("UPDATE rules SET category_id = %s WHERE id = %s", (basics["sub2"], rule_id))
    assert apply_rules(conn).changed == 1
    assert conn.execute("SELECT category_id FROM transactions WHERE id = %s", (tid,)).fetchone()[0] == basics["sub2"]

    conn.execute("UPDATE rules SET active = false WHERE id = %s", (rule_id,))
    assert apply_rules(conn).uncategorized == 1
    assert conn.execute("SELECT category_id, category_source, rule_id FROM transactions WHERE id = %s", (tid,)).fetchone() == (None, None, None)


def test_deleted_rule_rows_go_back_to_review(conn, add_card_txn, basics):
    tid = add_card_txn(raw_description="FAKE COFFEE SHOP")
    rule_id = create_rule(conn, "coffee", basics["sub"])
    apply_rules(conn)
    conn.execute("DELETE FROM rules WHERE id = %s", (rule_id,))
    assert apply_rules(conn).uncategorized == 1
    assert conn.execute("SELECT category_id FROM transactions WHERE id = %s", (tid,)).fetchone()[0] is None


def test_categorize_manually_with_rule(conn, add_card_txn, basics):
    first = add_card_txn(raw_description="FAKE BAKERY 01")
    second = add_card_txn(raw_description="FAKE BAKERY 02")
    unrelated = add_card_txn(raw_description="FAKE GARAGE")

    rule_id, others = categorize_manually(conn, first, basics["sub"], save_rule_pattern="FAKE BAKERY")
    assert others == 1
    assert conn.execute("SELECT category_source FROM transactions WHERE id = %s", (first,)).fetchone()[0] == "manual"
    assert conn.execute("SELECT category_source, rule_id FROM transactions WHERE id = %s", (second,)).fetchone() == ("rule", rule_id)
    assert conn.execute("SELECT category_id FROM transactions WHERE id = %s", (unrelated,)).fetchone()[0] is None
    assert conn.execute("SELECT created_from_transaction_id FROM rules WHERE id = %s", (rule_id,)).fetchone()[0] == first


def test_categorize_manually_without_rule(conn, add_card_txn, basics):
    tid = add_card_txn(raw_description="FAKE BAKERY")
    assert categorize_manually(conn, tid, basics["sub"]) == (None, 0)
    assert conn.execute("SELECT count(*) FROM rules").fetchone()[0] == 0


def test_manual_can_replace_rule_category(conn, add_card_txn, basics):
    tid = add_card_txn(raw_description="FAKE COFFEE")
    create_rule(conn, "coffee", basics["sub"])
    apply_rules(conn)
    categorize_manually(conn, tid, basics["sub2"])
    assert conn.execute("SELECT category_id, category_source, rule_id FROM transactions WHERE id = %s", (tid,)).fetchone() == (basics["sub2"], "manual", None)
