"""Transfer matching (phase 10): automatic links, links by hand, generated legs, needs care."""

from pathlib import Path

import psycopg
import pytest

from budget.importer import commit_import, preview_import, undo_import
from budget.needs_care import needs_care_reason
from budget.rules import apply_rules, categorize_manually, create_rule
from budget.transactions import delete_manual, mark_as_transfer
from budget.transfers import (
    auto_match, candidates, dissolve, generate_leg, link, recent_links, unlink_by_hand, unmatched_legs,
)

CITI = Path(__file__).parent / "fixtures" / "citi" / "sample.csv"


@pytest.fixture
def accts(conn, basics):
    """The basics card (with a parser), a bank account with a parser, a savings account and Cash."""
    q = lambda sql, *args: conn.execute(sql, args).fetchone()[0]  # noqa: E731
    bank = q("INSERT INTO accounts (display_name, institution, owner_person_id, parser_key, account_class, "
             "account_type) VALUES ('Bank', 'Fake', %s, 'bcu', 'asset', 'checking') RETURNING id", basics["person"])
    savings = q("INSERT INTO accounts (display_name, institution, owner_person_id, parser_key, account_class, "
                "account_type) VALUES ('Savings', 'Fake', %s, 'bcu', 'asset', 'savings') RETURNING id",
                basics["person"])
    cash = q("SELECT id FROM accounts WHERE account_type = 'cash'")
    return {"card": basics["card"], "bank": bank, "savings": savings, "cash": cash}


@pytest.fixture
def add(add_card_txn):
    """add(account, amount, date, kind='transfer' | 'expense' | None, ...) -> id."""
    def _add(account_id, amount_cents, txn_date="2026-01-15", kind="transfer", **extra):
        return add_card_txn(account_id=account_id, amount_cents=amount_cents, txn_date=txn_date,
                            posted_date=None, kind=kind, kind_source="parser" if kind else None, **extra)
    return _add


def row(conn, txn_id):
    return conn.execute("SELECT kind, kind_source, transfer_id IS NOT NULL FROM transactions WHERE id = %s",
                        (txn_id,)).fetchone()


# ---- automatic matching ------------------------------------------------------

def test_card_payment_links_to_unknown_bank_row(conn, accts, add):
    card_leg = add(accts["card"], -50000)                       # payment received on the card
    bank_leg = add(accts["bank"], 50000, "2026-01-13", kind=None)  # money out of checking, not classified
    assert auto_match(conn) == 1
    assert row(conn, card_leg) == ("transfer", "parser", True)
    assert row(conn, bank_leg) == ("transfer", "match", True)
    assert conn.execute("SELECT matched_by FROM transfers").fetchone()[0] == "auto"
    assert conn.execute("SELECT count(*) FROM spending_lines").fetchone()[0] == 0


def test_two_candidates_wait_for_a_person(conn, accts, add):
    leg = add(accts["card"], -50000)
    add(accts["bank"], 50000, kind=None)
    add(accts["savings"], 50000, kind=None)
    assert auto_match(conn) == 0
    assert len(candidates(conn, leg)) == 2
    assert [u.id for u in unmatched_legs(conn)] == [leg]


def test_partner_with_another_option_isnt_linked(conn, accts, add):
    add(accts["card"], -50000)
    add(accts["savings"], -50000)                  # a second transfer leg the bank row could also belong to
    add(accts["bank"], 50000, kind=None)
    assert auto_match(conn) == 0


def test_two_unknown_rows_are_never_linked_automatically(conn, accts, add):
    add(accts["bank"], 50000, kind=None)
    add(accts["savings"], -50000, kind=None)
    assert auto_match(conn) == 0


def test_window_and_amount(conn, accts, add):
    leg = add(accts["card"], -50000, "2026-01-15")
    far = add(accts["bank"], 50000, "2026-01-22", kind=None)     # 7 days apart
    add(accts["bank"], 49999, "2026-01-15", kind=None)           # wrong amount
    add(accts["card"], 50000, "2026-01-15", kind=None)           # same account
    assert auto_match(conn) == 0
    assert [c.id for c in candidates(conn, leg, manual=True)] == [far]   # a person may still pick it


def test_expense_rows_are_candidates_only_for_a_person(conn, accts, add):
    leg = add(accts["card"], -50000)
    expense = add(accts["bank"], 50000, kind="expense")
    assert auto_match(conn) == 0
    assert [c.id for c in candidates(conn, leg, manual=True)] == [expense]


def test_rule_counterpart_narrows_candidates(conn, accts, add):
    rule_id = create_rule(conn, "PMT TO CARD", None, is_transfer=True, account_id=accts["bank"],
                          counterpart_account_id=accts["card"])
    leg = add(accts["bank"], 50000, raw_description="PMT TO CARD")
    conn.execute("UPDATE transactions SET kind_source = 'rule', rule_id = %s WHERE id = %s", (rule_id, leg))
    card = add(accts["card"], -50000)
    add(accts["savings"], -50000, kind=None)       # would make it ambiguous without the hint
    assert auto_match(conn) == 1
    assert row(conn, card)[2] is True
    assert conn.execute("SELECT matched_by FROM transfers").fetchone()[0] == "rule"


def test_hand_marked_transfer_is_matched_right_away(conn, accts, add):
    card_leg = add(accts["card"], -50000)
    bank = add(accts["bank"], 50000, kind="expense")
    mark_as_transfer(conn, bank)                   # e.g. from Review or Transactions
    assert row(conn, bank) == ("transfer", "manual", True)
    assert row(conn, card_leg)[2] is True


def test_needs_care_rows_are_not_linked_automatically(conn, accts, add):
    add(accts["card"], -50000)
    add(accts["bank"], 50000, kind=None, needs_care="Venmo / Zelle")
    assert auto_match(conn) == 0


# ---- undoing links -----------------------------------------------------------

def test_dissolving_returns_matched_row_to_unknown(conn, accts, add):
    card_leg = add(accts["card"], -50000)
    bank_leg = add(accts["bank"], 50000, kind=None)
    auto_match(conn)
    dissolve(conn, conn.execute("SELECT id FROM transfers").fetchone()[0])
    assert row(conn, bank_leg) == (None, None, False)
    assert row(conn, card_leg) == ("transfer", "parser", False)


def test_unlink_by_hand_stops_relinking(conn, accts, add):
    card_leg = add(accts["card"], -50000)
    bank_leg = add(accts["bank"], 50000, kind=None)
    auto_match(conn)
    unlink_by_hand(conn, conn.execute("SELECT id FROM transfers").fetchone()[0])
    assert auto_match(conn) == 0
    # A person can still link them; that clears the block.
    link(conn, card_leg, bank_leg)
    assert row(conn, bank_leg) == ("transfer", "manual", True)
    assert conn.execute("SELECT bool_or(no_auto_match) FROM transactions").fetchone()[0] is False


def test_categorizing_a_leg_unlinks_it(conn, accts, add, basics):
    card_leg = add(accts["card"], -50000)
    bank_leg = add(accts["bank"], 50000, kind=None)
    auto_match(conn)
    categorize_manually(conn, bank_leg, basics["sub"])
    assert row(conn, bank_leg) == ("expense", "manual", False)
    assert row(conn, card_leg) == ("transfer", "parser", False)


def test_link_checks(conn, accts, add):
    a = add(accts["card"], -50000)
    with pytest.raises(ValueError, match="different accounts"):
        link(conn, a, add(accts["card"], 50000))
    with pytest.raises(ValueError, match="opposite amounts"):
        link(conn, a, add(accts["bank"], 40000))
    link(conn, a, add(accts["bank"], 50000))
    with pytest.raises(ValueError, match="already linked"):
        link(conn, a, add(accts["savings"], 50000))


def test_recent_links_reads_from_and_to(conn, accts, add):
    add(accts["card"], -50000)
    add(accts["bank"], 50000, kind=None, merchant="PMT TO CARD")
    auto_match(conn)
    [t] = recent_links(conn, since=__import__("datetime").date(2026, 1, 1))
    assert (t.from_account, t.to_account, t.amount_cents, t.description) == ("Bank", "Test Card", 50000, "PMT TO CARD")


# ---- generated legs (accounts without a CSV) ---------------------------------

def test_generated_leg_in_cash(conn, accts, add):
    atm = add(accts["bank"], 6000, "2026-01-20", kind=None)    # $60 out of checking
    cash_leg = generate_leg(conn, atm, accts["cash"])
    got = conn.execute("SELECT account_id, amount_cents, txn_date::text, merchant, kind, source, generated_from_id "
                       "FROM transactions WHERE id = %s", (cash_leg,)).fetchone()
    assert got == (accts["cash"], -6000, "2026-01-20", "From Bank", "transfer", "manual", atm)
    assert row(conn, atm) == ("transfer", "manual", True)


def test_generated_leg_goes_with_its_link(conn, accts, add, basics):
    atm = add(accts["bank"], 6000, kind=None)
    cash_leg = generate_leg(conn, atm, accts["cash"])
    with pytest.raises(ValueError, match="other half of a transfer"):
        categorize_manually(conn, cash_leg, basics["sub"])
    with pytest.raises(ValueError, match="other half of a transfer"):
        delete_manual(conn, cash_leg)
    categorize_manually(conn, atm, basics["sub"])          # it wasn't a transfer after all
    assert conn.execute("SELECT count(*) FROM transactions WHERE id = %s", (cash_leg,)).fetchone()[0] == 0


def test_generated_leg_needs_an_account_without_csv(conn, accts, add):
    leg = add(accts["bank"], 6000)
    with pytest.raises(ValueError, match="its own CSV"):
        generate_leg(conn, leg, accts["savings"])


def test_rule_counterpart_without_csv_generates_the_leg(conn, accts, add):
    rule_id = create_rule(conn, "ATM", None, is_transfer=True, account_id=accts["bank"],
                          counterpart_account_id=accts["cash"])
    leg = add(accts["bank"], 6000, raw_description="FAKE ATM WITHDRAWAL")
    conn.execute("UPDATE transactions SET kind_source = 'rule', rule_id = %s WHERE id = %s", (rule_id, leg))
    assert auto_match(conn) == 1
    assert conn.execute("SELECT amount_cents FROM transactions WHERE generated_from_id = %s", (leg,)).fetchone() \
        == (-6000,)


# ---- needs care --------------------------------------------------------------

@pytest.mark.parametrize("text, reason", [
    ("ATM WITHDRAWAL 123 MAIN ST", "ATM / cash"),
    ("ATM FEE", None),
    ("VENMO PAYMENT 1234", "Venmo / Zelle"),
    ("Zelle to FAKE PERSON", "Venmo / Zelle"),
    ("CASH APP*FAKE", "Venmo / Zelle"),
    ("CHECK 1042", "paper check"),
    ("CHECK #1042", "paper check"),
    ("CHECKCARD 1234 FAKE STORE", None),
    ("FAKE GROCER", None),
])
def test_needs_care_reason(text, reason):
    assert needs_care_reason(text) == reason


def test_parser_transfers_never_need_care():
    assert needs_care_reason("ZELLE PAYMENT - THANK YOU", "transfer") is None


def test_rules_skip_rows_needing_care(conn, add_card_txn, basics):
    old = add_card_txn(raw_description="VENMO *FAKE FRIEND", kind="expense")   # imported before the check
    create_rule(conn, "venmo", basics["sub"])
    apply_rules(conn)
    assert conn.execute("SELECT needs_care, category_id FROM transactions WHERE id = %s", (old,)).fetchone() \
        == ("Venmo / Zelle", None)


# ---- through the importer ----------------------------------------------------

def test_import_links_card_payment_and_undo_unlinks(conn, basics, tmp_path):
    card = conn.execute(
        "INSERT INTO accounts (display_name, institution, owner_person_id, parser_key, account_class, account_type) "
        "VALUES ('Citi', 'Citi', %s, 'citi', 'liability', 'credit_card') RETURNING id", (basics["person"],),
    ).fetchone()[0]
    bank = conn.execute(
        "INSERT INTO accounts (display_name, institution, owner_person_id, parser_key, account_class, account_type) "
        "VALUES ('Bank', 'Fake', %s, 'bcu', 'asset', 'checking') RETURNING id", (basics["person"],),
    ).fetchone()[0]
    bank_batch = conn.execute(
        "INSERT INTO import_batches (account_id, filename, file_sha256, rows_in_file, rows_inserted, "
        "rows_duplicate, rows_excluded) VALUES (%s, 'b.csv', %s, 1, 1, 0, 0) RETURNING id", (bank, "b" * 64),
    ).fetchone()[0]
    # Checking paid the card $500 on 10/17 (the Citi file shows it received on 10/18).
    bank_leg = conn.execute(
        "INSERT INTO transactions (source, account_id, txn_date, amount_cents, raw_description, purchaser_id, "
        "fingerprint, import_batch_id) VALUES ('import', %s, '2026-10-17', 50000, 'ONLINE PMT CITI', %s, 'fp-b', %s) "
        "RETURNING id", (bank, basics["person"], bank_batch),
    ).fetchone()[0]

    batch = commit_import(conn, preview_import(conn, card, "citi.csv", CITI.read_bytes()), archive=tmp_path)
    assert row(conn, bank_leg) == ("transfer", "match", True)
    undo_import(conn, batch)
    assert row(conn, bank_leg) == (None, None, False)


def test_import_flags_needs_care_and_skips_rules(conn, basics, tmp_path):
    create_rule(conn, "venmo", basics["sub"])
    bank = conn.execute(
        "INSERT INTO accounts (display_name, institution, owner_person_id, parser_key, account_class, account_type) "
        "VALUES ('Bank', 'Fake', %s, 'bcu', 'asset', 'checking') RETURNING id", (basics["person"],),
    ).fetchone()[0]
    text = ("Account ID,Transaction ID,Date,Name,Description,Check Number,Category,Tags,Amount,Balance\n"
            '1,1,11/04/26,,Withdrawal VENMO *FAKE FRIEND,,,,"-$20.00","$100.00"\n')
    p = preview_import(conn, bank, "v.csv", text.encode())
    [n] = p.new_rows
    assert (n.needs_care, n.rule, n.label, p.needs_care_count, p.uncategorized_count) == \
        ("Venmo / Zelle", None, "needs care (Venmo / Zelle)", 1, 0)
    batch = commit_import(conn, p, archive=tmp_path)
    assert conn.execute("SELECT needs_care, category_id FROM transactions WHERE import_batch_id = %s",
                        (batch,)).fetchone() == ("Venmo / Zelle", None)


def test_deleting_the_original_deletes_the_generated_leg(committed_database_url):
    """Undoing an import removes a generated Cash leg too (checked with real commits)."""
    with psycopg.connect(committed_database_url) as conn:
        person = conn.execute("INSERT INTO people (name) VALUES ('Gen') RETURNING id").fetchone()[0]
        bank = conn.execute(
            "INSERT INTO accounts (display_name, institution, owner_person_id, parser_key, account_class, "
            "account_type) VALUES ('Gen Bank', 'Fake', %s, 'bcu', 'asset', 'checking') RETURNING id", (person,),
        ).fetchone()[0]
        cash = conn.execute("SELECT id FROM accounts WHERE account_type = 'cash'").fetchone()[0]
        batch = conn.execute(
            "INSERT INTO import_batches (account_id, filename, file_sha256, rows_in_file, rows_inserted, "
            "rows_duplicate, rows_excluded) VALUES (%s, 'g.csv', %s, 1, 1, 0, 0) RETURNING id", (bank, "e" * 64),
        ).fetchone()[0]
        atm = conn.execute(
            "INSERT INTO transactions (source, account_id, txn_date, amount_cents, raw_description, purchaser_id, "
            "fingerprint, import_batch_id) VALUES ('import', %s, '2026-01-20', 6000, 'ATM', %s, 'fp-gen', %s) "
            "RETURNING id", (bank, person, batch),
        ).fetchone()[0]
        conn.commit()
        generate_leg(conn, atm, cash)
        conn.commit()
        undo_import(conn, batch)
        conn.commit()
        assert conn.execute("SELECT count(*) FROM transactions WHERE generated_from_id IS NOT NULL").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM transfers").fetchone()[0] == 0
