from pathlib import Path

import pytest

from budget.importer import commit_import, detect_card, preview_import, undo_import
from budget.rules import create_rule

FIXTURE = Path(__file__).parent / "fixtures" / "citi" / "sample.csv"


@pytest.fixture
def citi_card(conn):
    person = conn.execute("INSERT INTO people (name) VALUES ('Importer Person') RETURNING id").fetchone()[0]
    card = conn.execute(
        "INSERT INTO cards (display_name, issuer, last4, owner_person_id, parser_key) "
        "VALUES ('Fake Citi', 'Citi', '0000', %s, 'citi') RETURNING id",
        (person,),
    ).fetchone()[0]
    return {"card": card, "person": person}


def test_preview_counts(conn, citi_card):
    p = preview_import(conn, citi_card["card"], "sample.csv", FIXTURE.read_bytes())
    assert p.rows_in_file == 8
    assert len(p.new_rows) == 5          # 3 purchases incl. two identical coffees, 1 big one, 1 refund
    assert len(p.duplicate_rows) == 0
    assert len(p.excluded_rows) == 3     # 2 payments + 1 pending
    assert p.uncategorized_count == 5


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
    assert row == (8, 5, 0, 3)

    txn = conn.execute(
        "SELECT amount_cents, merchant, purchaser_id, source, category_id, raw_row->>'Debit' "
        "FROM transactions WHERE import_batch_id = %s ORDER BY txn_date DESC, id LIMIT 1",
        (batch,),
    ).fetchone()
    assert txn == (4217, "FAKE AUTO PARTS", citi_card["person"], "card", None, "$42.17 ")

    archived = tmp_path / f"{batch:06d}_sample.csv"
    assert archived.read_bytes() == FIXTURE.read_bytes()


def test_reimport_is_all_duplicates(conn, citi_card, tmp_path):
    data = FIXTURE.read_bytes()
    commit_import(conn, preview_import(conn, citi_card["card"], "sample.csv", data), archive=tmp_path)

    again = preview_import(conn, citi_card["card"], "sample.csv", data)
    assert len(again.new_rows) == 0
    assert len(again.duplicate_rows) == 5
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
    assert len(p.duplicate_rows) == 5


def test_undo_deletes_rows_and_allows_reimport(conn, citi_card, tmp_path):
    data = FIXTURE.read_bytes()
    batch = commit_import(conn, preview_import(conn, citi_card["card"], "sample.csv", data), archive=tmp_path)

    assert undo_import(conn, batch) == 5
    assert conn.execute("SELECT count(*) FROM transactions WHERE import_batch_id = %s", (batch,)).fetchone()[0] == 0
    assert conn.execute("SELECT reverted_at IS NOT NULL FROM import_batches WHERE id = %s", (batch,)).fetchone()[0]

    with pytest.raises(ValueError, match="already undone"):
        undo_import(conn, batch)

    again = preview_import(conn, citi_card["card"], "sample.csv", data)
    assert len(again.new_rows) == 5
    assert again.already_imported_batch_ids == []


def test_unknown_parser_key(conn, citi_card):
    conn.execute("UPDATE cards SET parser_key = 'nope' WHERE id = %s", (citi_card["card"],))
    with pytest.raises(ValueError, match="No parser"):
        preview_import(conn, citi_card["card"], "sample.csv", FIXTURE.read_bytes())


def test_detect_card_single_match(conn, citi_card):
    assert detect_card(conn, FIXTURE.read_bytes()) == citi_card["card"]


def test_detect_card_two_citi_cards_is_unsure(conn, citi_card):
    conn.execute(
        "INSERT INTO cards (display_name, issuer, last4, owner_person_id, parser_key) "
        "VALUES ('Other Citi', 'Citi', '5555', %s, 'citi')",
        (citi_card["person"],),
    )
    assert detect_card(conn, FIXTURE.read_bytes()) is None


def test_detect_card_ignores_masked_number(conn, citi_card):
    # The fixture's masked number is 0000; a card with different last4 still matches.
    conn.execute("UPDATE cards SET last4 = '4321' WHERE id = %s", (citi_card["card"],))
    assert detect_card(conn, FIXTURE.read_bytes()) == citi_card["card"]


def test_detect_card_unreadable_file(conn, citi_card):
    assert detect_card(conn, b"Date,Amount\n01/01/2026,1.00\n") is None


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


def test_detect_card_picks_by_bank_format(conn, citi_card):
    chase_card = conn.execute(
        "INSERT INTO cards (display_name, issuer, last4, owner_person_id, parser_key) "
        "VALUES ('Fake Chase', 'Chase', '2222', %s, 'chase') RETURNING id",
        (citi_card["person"],),
    ).fetchone()[0]
    chase_file = (Path(__file__).parent / "fixtures" / "chase" / "sample.csv").read_bytes()
    assert detect_card(conn, FIXTURE.read_bytes()) == citi_card["card"]
    assert detect_card(conn, chase_file) == chase_card


def test_chase_import_uses_transaction_date_and_keeps_post_date(conn, citi_card, tmp_path):
    card = conn.execute(
        "INSERT INTO cards (display_name, issuer, owner_person_id, parser_key) "
        "VALUES ('Fake Chase 2', 'Chase', %s, 'chase') RETURNING id",
        (citi_card["person"],),
    ).fetchone()[0]
    data = (Path(__file__).parent / "fixtures" / "chase" / "sample.csv").read_bytes()
    p = preview_import(conn, card, "chase.csv", data)
    assert (len(p.new_rows), len(p.excluded_rows)) == (6, 2)
    batch = commit_import(conn, p, archive=tmp_path)
    row = conn.execute(
        "SELECT txn_date, posted_date, effective_date, amount_cents FROM transactions "
        "WHERE import_batch_id = %s AND raw_description = 'FAKE CAFE'", (batch,),
    ).fetchone()
    assert [str(v) for v in row[:3]] + [row[3]] == ["2026-10-29", "2026-10-30", "2026-10-29", 888]
    again = preview_import(conn, card, "chase.csv", data)
    assert (len(again.new_rows), len(again.duplicate_rows)) == (0, 6)
