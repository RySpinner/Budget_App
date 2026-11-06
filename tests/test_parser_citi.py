from datetime import date
from pathlib import Path

import pytest

from budget.parsers import citi, get_parser

FIXTURE = Path(__file__).parent / "fixtures" / "citi" / "sample.csv"


@pytest.fixture
def rows():
    return citi.parse(FIXTURE.read_text())


def test_registered():
    assert get_parser("citi") is citi.parse


def test_blank_rows_skipped(rows):
    assert len(rows) == 8


def test_purchase(rows):
    r = rows[0]
    assert r.kind == "expense"
    assert r.txn_date == date(2026, 10, 26)
    assert r.posted_date is None
    assert r.amount_cents == 4217
    assert r.raw_description == "FAKE AUTO PARTS 5550001234 CT null XXXXXXXXXXXX0000"
    assert r.raw_row["Debit"] == "$42.17 "  # raw text kept exactly


def test_thousands_separator(rows):
    assert rows[3].amount_cents == 102399


def test_refund_is_negative(rows):
    assert rows[4].kind == "expense"
    assert rows[4].amount_cents == -1525


def test_payments_are_transfers(rows):
    assert [(r.kind, r.reason, r.amount_cents) for r in rows[5:7]] == [
        ("transfer", "card payment", -50000),
        ("transfer", "card payment", -25000),  # credit written without a minus sign
    ]


def test_pending_excluded(rows):
    assert rows[7].kind == "excluded"
    assert rows[7].reason == "pending"


def test_wrong_file_rejected():
    with pytest.raises(ValueError, match="Citi"):
        citi.parse("Date,Amount,Description\n01/01/2026,1.00,X\n")


def test_bad_row_reports_line_number():
    text = "Status,Date,Description,Debit,Credit\nCleared,13/45/2026,X,$1.00,\n"
    with pytest.raises(ValueError, match="Line 2"):
        citi.parse(text)



# ---- unquoted commas (seen in a real export) --------------------------------

SPLIT_FIXTURE = Path(__file__).parent / "fixtures" / "citi" / "split_amounts.csv"


@pytest.fixture
def split_rows():
    return citi.parse(SPLIT_FIXTURE.read_text())


def test_trailing_comma_header_accepted(split_rows):
    assert len(split_rows) == 5


def test_normal_row_with_extra_blank_column(split_rows):
    r = split_rows[0]
    assert (r.kind, r.amount_cents) == ("expense", 3864)


def test_payment_split_across_columns(split_rows):
    r = split_rows[1]
    assert (r.kind, r.reason, r.amount_cents) == ("transfer", "card payment", -123456)
    assert r.raw_description == "AUTOPAY 000000000000000RAUTOPAY AUTO-PMT"
    # The original pieces are kept as read.
    assert r.raw_row["Credit"] == "($1)"
    assert r.raw_row["_extra"] == ["234.56"]


def test_debit_split_across_columns(split_rows):
    r = split_rows[2]
    assert (r.kind, r.amount_cents) == ("expense", 234567)
    assert r.raw_description == "FAKE FURNITURE STORE null XXXXXXXXXXXX0000"


def test_comma_inside_description(split_rows):
    purchase, refund = split_rows[3], split_rows[4]
    assert (purchase.raw_description, purchase.amount_cents) == ("FAKE STORE, INC null XXXXXXXXXXXX0000", 1200)
    assert (refund.kind, refund.raw_description, refund.amount_cents) == (
        "expense", "FAKE STORE, INC RETURN null XXXXXXXXXXXX0000", -1200)


@pytest.mark.parametrize("line, cents", [
    ("Cleared,01/02/2026,X,,($1,587.82)", -158782),     # if Citi ever keeps the parentheses together
    ("Cleared,01/02/2026,X,,-1,587.82", -158782),
    ("Cleared,01/02/2026,X,$12,345,678.90,", 1234567890),
    ("Cleared,01/02/2026,X,,$5.00", -500),
])
def test_amount_shapes(line, cents):
    [row] = citi.parse("Status,Date,Description,Debit,Credit\n" + line + "\n")
    assert row.amount_cents == cents


def test_garbage_amount_rejected():
    with pytest.raises(ValueError, match="Line 2"):
        citi.parse("Status,Date,Description,Debit,Credit\nCleared,01/02/2026,X,abc,\n")


# ---- Citi's own format, as downloaded (never opened in Excel) ---------------
# Descriptions are quoted; amounts are not, and credits look like " -$ 1,234.56".

RAW_FIXTURE = Path(__file__).parent / "fixtures" / "citi" / "raw_download.csv"


def test_raw_download_format():
    rows = citi.parse(RAW_FIXTURE.read_text())
    assert [(r.kind, r.reason, r.amount_cents) for r in rows] == [
        ("expense", None, 3864),
        ("transfer", "card payment", -123456),
        ("expense", None, 234567),
        ("expense", None, -1525),
    ]
    assert rows[2].raw_description == "FAKE FURNITURE STORE, INC null XXXXXXXXXXXX0000"


def test_excel_saved_and_raw_versions_dedup_to_the_same_fingerprint():
    """Importing a file and an Excel-saved copy of it must not create duplicates."""
    from budget.importer import fingerprint_rows
    raw = citi.parse("Status,Date,Description,Debit,Credit\n"
                     'Cleared,10/01/2026,"AUTOPAY X AUTO-PMT",, -$ 1,234.56\n'
                     'Cleared,10/02/2026,"FAKE SHOP",$5.00 ,\n')
    excel = citi.parse("Status,Date,Description,Debit,Credit,\n"
                       "Cleared,10/1/2026,AUTOPAY X AUTO-PMT,,($1),234.56\n"
                       "Cleared,10/2/2026,FAKE SHOP,$5.00 ,,\n")
    assert [fp for _, fp in fingerprint_rows(1, raw)] == [fp for _, fp in fingerprint_rows(1, excel)]


def test_rewards_still_excluded():
    text = "Status,Date,Description,Debit,Credit\nCleared,01/02/2026,CASH REWARD REDEMPTION,,-25.00\n"
    [row] = citi.parse(text)
    assert (row.kind, row.reason) == ("excluded", "rewards redemption")


def test_describe_labels():
    from budget.parsers.base import describe
    assert describe("expense", 100) == "purchase"
    assert describe("expense", -100) == "refund"
    assert describe("transfer", -100, "card payment") == "transfer (card payment)"
    assert describe("excluded", 0, "pending") == "excluded: pending"
    assert describe(None, 5) == "needs review"
