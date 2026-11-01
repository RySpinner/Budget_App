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
    assert r.kind == "purchase"
    assert r.txn_date == date(2026, 10, 26)
    assert r.posted_date is None
    assert r.amount_cents == 4217
    assert r.raw_description == "FAKE AUTO PARTS 5550001234 CT null XXXXXXXXXXXX0000"
    assert r.raw_row["Debit"] == "$42.17 "  # raw text kept exactly


def test_thousands_separator(rows):
    assert rows[3].amount_cents == 102399


def test_refund_is_negative(rows):
    assert rows[4].kind == "refund"
    assert rows[4].amount_cents == -1525


def test_payments_excluded(rows):
    assert [(r.kind, r.exclude_reason, r.amount_cents) for r in rows[5:7]] == [
        ("excluded", "card payment", -50000),
        ("excluded", "card payment", -25000),  # credit written without a minus sign
    ]


def test_pending_excluded(rows):
    assert rows[7].kind == "excluded"
    assert rows[7].exclude_reason == "pending"


def test_wrong_file_rejected():
    with pytest.raises(ValueError, match="Citi"):
        citi.parse("Date,Amount,Description\n01/01/2026,1.00,X\n")


def test_bad_row_reports_line_number():
    text = "Status,Date,Description,Debit,Credit\nCleared,13/45/2026,X,$1.00,\n"
    with pytest.raises(ValueError, match="Line 2"):
        citi.parse(text)
