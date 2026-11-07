from datetime import date
from pathlib import Path

import pytest

from budget.importer import commit_import, detect_account, preview_import, undo_import
from budget.rules import create_rule

FIXTURE = Path(__file__).parent / "fixtures" / "citi" / "sample.csv"


@pytest.fixture
def citi_card(conn):
    person = conn.execute("INSERT INTO people (name) VALUES ('Importer Person') RETURNING id").fetchone()[0]
    card = conn.execute(
        "INSERT INTO accounts (display_name, institution, last4, owner_person_id, parser_key, account_class, account_type) "
        "VALUES ('Fake Citi', 'Citi', '0000', %s, 'citi', 'liability', 'credit_card') RETURNING id",
        (person,),
    ).fetchone()[0]
    return {"card": card, "person": person}


def test_preview_counts(conn, citi_card):
    p = preview_import(conn, citi_card["card"], "sample.csv", FIXTURE.read_bytes())
    assert p.rows_in_file == 8
    assert len(p.new_rows) == 7          # 4 purchases incl. two identical coffees, 1 refund, 2 card payments
    assert len(p.duplicate_rows) == 0
    assert len(p.excluded_rows) == 1     # the pending row
    assert p.uncategorized_count == 5    # card payments are transfers: they never need a category
    assert p.transfer_count == 2


def test_preview_writes_nothing(conn, citi_card):
    preview_import(conn, citi_card["card"], "sample.csv", FIXTURE.read_bytes())
    assert conn.execute("SELECT count(*) FROM transactions").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM import_batches").fetchone()[0] == 0


def test_identical_rows_in_one_file_both_kept(conn, citi_card):
    p = preview_import(conn, citi_card["card"], "sample.csv", FIXTURE.read_bytes())
    coffees = [n for n in p.new_rows if "COFFEE" in n.parsed.raw_description]
    assert len(coffees) == 2
    assert coffees[0].fingerprint != coffees[1].fingerprint


def test_commit_inserts_and_archives(conn, citi_card, tmp_path):
    p = preview_import(conn, citi_card["card"], "sample.csv", FIXTURE.read_bytes())
    batch = commit_import(conn, p, archive=tmp_path)

    row = conn.execute(
        "SELECT rows_in_file, rows_inserted, rows_duplicate, rows_excluded FROM import_batches WHERE id = %s",
        (batch,),
    ).fetchone()
    assert row == (8, 7, 0, 1)

    txn = conn.execute(
        "SELECT amount_cents, merchant, purchaser_id, source, category_id, raw_row->>'Debit' "
        "FROM transactions WHERE import_batch_id = %s ORDER BY txn_date DESC, id LIMIT 1",
        (batch,),
    ).fetchone()
    assert txn == (4217, "FAKE AUTO PARTS", citi_card["person"], "import", None, "$42.17 ")

    archived = tmp_path / f"{batch:06d}_sample.csv"
    assert archived.read_bytes() == FIXTURE.read_bytes()


def test_reimport_is_all_duplicates(conn, citi_card, tmp_path):
    data = FIXTURE.read_bytes()
    commit_import(conn, preview_import(conn, citi_card["card"], "sample.csv", data), archive=tmp_path)

    again = preview_import(conn, citi_card["card"], "sample.csv", data)
    assert len(again.new_rows) == 0
    assert len(again.duplicate_rows) == 7
    assert again.already_imported_batch_ids


def test_overlapping_file_only_adds_new_rows(conn, citi_card, tmp_path):
    data = FIXTURE.read_bytes()
    commit_import(conn, preview_import(conn, citi_card["card"], "sample.csv", data), archive=tmp_path)

    # A later export: everything from before plus one new charge and a third identical coffee.
    extra = (
        b"Cleared,10/28/2026,FAKE BOOKSHOP null XXXXXXXXXXXX0000,$9.99 ,\r\n"
        b"Cleared,10/24/2026,SQ *FAKE COFFEE ROASTERS SPRINGFIELD IL null XXXXXXXXXXXX0000,$4.50 ,\r\n"
    )
    header, rest = data.split(b"\r\n", 1)
    later = header + b"\r\n" + extra + rest
    p = preview_import(conn, citi_card["card"], "later.csv", later)
    assert sorted(n.parsed.amount_cents for n in p.new_rows) == [450, 999]
    assert len(p.duplicate_rows) == 7


def test_undo_deletes_rows_and_allows_reimport(conn, citi_card, tmp_path):
    data = FIXTURE.read_bytes()
    batch = commit_import(conn, preview_import(conn, citi_card["card"], "sample.csv", data), archive=tmp_path)

    assert undo_import(conn, batch) == 7
    assert conn.execute("SELECT count(*) FROM transactions WHERE import_batch_id = %s", (batch,)).fetchone()[0] == 0
    assert conn.execute("SELECT reverted_at IS NOT NULL FROM import_batches WHERE id = %s", (batch,)).fetchone()[0]

    with pytest.raises(ValueError, match="already undone"):
        undo_import(conn, batch)

    again = preview_import(conn, citi_card["card"], "sample.csv", data)
    assert len(again.new_rows) == 7
    assert again.already_imported_batch_ids == []


def test_unknown_parser_key(conn, citi_card):
    conn.execute("UPDATE accounts SET parser_key = 'nope' WHERE id = %s", (citi_card["card"],))
    with pytest.raises(ValueError, match="No parser"):
        preview_import(conn, citi_card["card"], "sample.csv", FIXTURE.read_bytes())


def test_detect_account_single_match(conn, citi_card):
    assert detect_account(conn, FIXTURE.read_bytes()) == citi_card["card"]


def test_detect_account_two_citi_cards_is_unsure(conn, citi_card):
    conn.execute(
        "INSERT INTO accounts (display_name, institution, last4, owner_person_id, parser_key, account_class, account_type) "
        "VALUES ('Other Citi', 'Citi', '5555', %s, 'citi', 'liability', 'credit_card')",
        (citi_card["person"],),
    )
    assert detect_account(conn, FIXTURE.read_bytes()) is None


def test_detect_account_ignores_masked_number(conn, citi_card):
    # The fixture's masked number is 0000; a card with different last4 still matches.
    conn.execute("UPDATE accounts SET last4 = '4321' WHERE id = %s", (citi_card["card"],))
    assert detect_account(conn, FIXTURE.read_bytes()) == citi_card["card"]


def test_detect_account_unreadable_file(conn, citi_card):
    assert detect_account(conn, b"Date,Amount\n01/01/2026,1.00\n") is None


def test_rules_apply_during_import(conn, citi_card, tmp_path):
    top = conn.execute("INSERT INTO categories (name) VALUES ('Imp Top') RETURNING id").fetchone()[0]
    coffee = conn.execute("INSERT INTO categories (name, parent_id) VALUES ('Imp Coffee', %s) RETURNING id", (top,)).fetchone()[0]
    rule_id = create_rule(conn, "coffee roasters", coffee, merchant_name="Fake Coffee")

    p = preview_import(conn, citi_card["card"], "sample.csv", FIXTURE.read_bytes())
    assert p.uncategorized_count == 3
    matched = [n for n in p.new_rows if n.rule]
    assert [n.merchant for n in matched] == ["Fake Coffee", "Fake Coffee"]

    batch = commit_import(conn, p, archive=tmp_path)
    rows = conn.execute(
        "SELECT category_id, category_source, rule_id, merchant FROM transactions "
        "WHERE import_batch_id = %s AND rule_id IS NOT NULL",
        (batch,),
    ).fetchall()
    assert rows == [(coffee, "rule", rule_id, "Fake Coffee")] * 2


def test_detect_account_picks_by_bank_format(conn, citi_card):
    chase_card = conn.execute(
        "INSERT INTO accounts (display_name, institution, last4, owner_person_id, parser_key, account_class, account_type) "
        "VALUES ('Fake Chase', 'Chase', '2222', %s, 'chase', 'liability', 'credit_card') RETURNING id",
        (citi_card["person"],),
    ).fetchone()[0]
    chase_file = (Path(__file__).parent / "fixtures" / "chase" / "sample.csv").read_bytes()
    assert detect_account(conn, FIXTURE.read_bytes()) == citi_card["card"]
    assert detect_account(conn, chase_file) == chase_card


def test_chase_import_uses_transaction_date_and_keeps_post_date(conn, citi_card, tmp_path):
    card = conn.execute(
        "INSERT INTO accounts (account_class, account_type, display_name, institution, owner_person_id, parser_key) "
        "VALUES ('liability', 'credit_card', 'Fake Chase 2', 'Chase', %s, 'chase') RETURNING id",
        (citi_card["person"],),
    ).fetchone()[0]
    data = (Path(__file__).parent / "fixtures" / "chase" / "sample.csv").read_bytes()
    p = preview_import(conn, card, "chase.csv", data)
    assert (len(p.new_rows), len(p.excluded_rows)) == (7, 1)   # the payment is now kept as a transfer
    batch = commit_import(conn, p, archive=tmp_path)
    row = conn.execute(
        "SELECT txn_date, posted_date, effective_date, amount_cents FROM transactions "
        "WHERE import_batch_id = %s AND raw_description = 'FAKE CAFE'", (batch,),
    ).fetchone()
    assert [str(v) for v in row[:3]] + [row[3]] == ["2026-10-29", "2026-10-30", "2026-10-29", 888]
    again = preview_import(conn, card, "chase.csv", data)
    assert (len(again.new_rows), len(again.duplicate_rows)) == (0, 7)



def test_card_payments_stored_as_transfers(conn, citi_card, tmp_path):
    batch = commit_import(conn, preview_import(conn, citi_card["card"], "sample.csv", FIXTURE.read_bytes()),
                          archive=tmp_path)
    rows = conn.execute(
        "SELECT kind, kind_source, category_id, transfer_id, amount_cents FROM transactions "
        "WHERE import_batch_id = %s AND kind = 'transfer' ORDER BY amount_cents", (batch,),
    ).fetchall()
    assert rows == [("transfer", "parser", None, None, -50000), ("transfer", "parser", None, None, -25000)]
    # Transfers never show up as spending.
    assert conn.execute("SELECT count(*) FROM spending_lines WHERE transaction_id IN "
                        "(SELECT id FROM transactions WHERE kind = 'transfer')").fetchone()[0] == 0
    assert conn.execute("SELECT kind, kind_source FROM transactions WHERE import_batch_id = %s AND amount_cents = 4217",
                        (batch,)).fetchone() == ("expense", "parser")


def test_rules_do_not_apply_to_transfer_rows_on_import(conn, citi_card):
    top = conn.execute("INSERT INTO categories (name) VALUES ('Imp T') RETURNING id").fetchone()[0]
    sub = conn.execute("INSERT INTO categories (name, parent_id) VALUES ('Imp S', %s) RETURNING id", (top,)).fetchone()[0]
    create_rule(conn, "AUTOPAY", sub)   # would match the payment row, but transfers are never categorized
    p = preview_import(conn, citi_card["card"], "sample.csv", FIXTURE.read_bytes())
    payment = next(n for n in p.new_rows if "AUTOPAY" in n.parsed.raw_description)
    assert (payment.rule, payment.kind) == (None, "transfer")


def test_hand_entered_account_cannot_import(conn, citi_card):
    cash = conn.execute("SELECT id FROM accounts WHERE account_type = 'cash'").fetchone()[0]
    with pytest.raises(ValueError, match="entered by hand"):
        preview_import(conn, cash, "x.csv", FIXTURE.read_bytes())


# ---------------------------------------------------------------------------
# Bank accounts (phase 9): BCU checking
# ---------------------------------------------------------------------------
FIXTURES = Path(__file__).parent / "fixtures" / "bcu"


@pytest.fixture
def bcu(conn, citi_card):
    q = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
    checking = q("INSERT INTO accounts (display_name, institution, owner_person_id, parser_key, account_class, "
                 "account_type) VALUES ('BCU checking', 'BCU', %s, 'bcu', 'asset', 'checking') RETURNING id",
                 citi_card["person"])
    card = q("INSERT INTO accounts (display_name, institution, owner_person_id, parser_key, account_class, "
             "account_type) VALUES ('BCU card', 'BCU', %s, 'bcu', 'liability', 'credit_card') RETURNING id",
             citi_card["person"])
    return {"checking": checking, "card": card}


def snapshots(conn, account_id):
    return conn.execute("SELECT as_of_date, balance_cents FROM balance_snapshots WHERE account_id = %s "
                        "ORDER BY as_of_date", (account_id,)).fetchall()


def test_checking_preview_kinds_and_balances(conn, bcu):
    p = preview_import(conn, bcu["checking"], "checking.csv", (FIXTURES / "checking.csv").read_bytes())
    assert len(p.new_rows) == 6                      # two identical cafe rows both kept
    assert [n.kind for n in p.new_rows] == ["income", "transfer", None, None, None, None]
    assert p.unknown_kind_count == 4
    assert p.transfer_count == 1
    # End of day: the file is newest first, so each day's first row is its last in time.
    assert p.balances == [(date(2026, 10, 31), 100000), (date(2026, 11, 1), 125000),
                          (date(2026, 11, 3), 100000), (date(2026, 11, 4), 300000)]


def test_card_rows_of_unknown_kind_become_expenses(conn, bcu):
    p = preview_import(conn, bcu["card"], "sample.csv", (FIXTURES / "sample.csv").read_bytes())
    kinds = {n.parsed.raw_description.split(" REF")[0]: n.kind for n in p.new_rows}
    assert kinds["PAYMENT - THANK YOU"] == "transfer"
    assert kinds["FAKE CAFE SPRINGFIELD IL"] == "expense"
    assert kinds["FAKE OUTDOOR STORE"] == "expense"         # a refund
    assert p.balances == []                                  # card balances aren't recorded yet


def test_checking_commit_saves_snapshots_and_undo_removes_them(conn, bcu, tmp_path):
    data = (FIXTURES / "checking.csv").read_bytes()
    batch = commit_import(conn, preview_import(conn, bcu["checking"], "checking.csv", data), archive=tmp_path)
    assert snapshots(conn, bcu["checking"])[-1] == (date(2026, 11, 4), 300000)
    assert len(snapshots(conn, bcu["checking"])) == 4
    kinds = dict(conn.execute("SELECT kind, count(*) FROM transactions WHERE account_id = %s GROUP BY kind",
                              (bcu["checking"],)).fetchall())
    assert kinds == {"income": 1, "transfer": 1, None: 4}
    undo_import(conn, batch)
    assert snapshots(conn, bcu["checking"]) == []


def test_later_import_replaces_a_days_balance(conn, bcu, tmp_path):
    header, *rows = (FIXTURES / "checking.csv").read_text().splitlines()
    early = "\n".join([header, rows[2]]).encode()          # 11/03 mid-day: $1,200.00
    commit_import(conn, preview_import(conn, bcu["checking"], "early.csv", early), archive=tmp_path)
    assert snapshots(conn, bcu["checking"]) == [(date(2026, 11, 3), 120000)]
    full = (FIXTURES / "checking.csv").read_bytes()
    commit_import(conn, preview_import(conn, bcu["checking"], "full.csv", full), archive=tmp_path)
    assert (date(2026, 11, 3), 100000) in snapshots(conn, bcu["checking"])


def test_unknown_checking_rows_show_in_review_not_spending(conn, bcu, tmp_path):
    data = (FIXTURES / "checking.csv").read_bytes()
    commit_import(conn, preview_import(conn, bcu["checking"], "checking.csv", data), archive=tmp_path)
    spending = conn.execute("SELECT count(*) FROM spending_lines WHERE account_id = %s", (bcu["checking"],)).fetchone()[0]
    assert spending == 0
    review = conn.execute("SELECT count(*) FROM transactions WHERE account_id = %s AND category_id IS NULL "
                          "AND kind IS DISTINCT FROM 'transfer'", (bcu["checking"],)).fetchone()[0]
    assert review == 5        # 4 unknown + the paycheck (income, still needs a category)
