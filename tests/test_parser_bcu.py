from datetime import date
from pathlib import Path

import pytest

from budget.parsers import bcu, get_parser

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = FIXTURES / "bcu" / "sample.csv"


@pytest.fixture
def rows():
    return bcu.parse(FIXTURE.read_text())


def test_registered():
    assert get_parser("bcu") is bcu.parse


def test_payment_is_transfer_with_thousands_comma(rows):
    r = rows[0]
    assert (r.kind, r.reason, r.amount_cents) == ("transfer", "card payment", -123456)
    assert r.txn_date == date(2026, 11, 3)          # two-digit year
    assert r.posted_date is None


def test_purchase_sign_is_flipped(rows):
    r = rows[1]
    assert (r.kind, r.amount_cents) == ("expense", 888)
    assert r.raw_description == "FAKE CAFE SPRINGFIELD IL REF #900000000000002"
    assert r.raw_row["Transaction ID"] == "2026-10-30:900000000000002:FAKE CAF"


def test_identical_rows_both_parsed(rows):
    assert [r.amount_cents for r in rows[2:4]] == [1250, 1250]


def test_positive_non_payment_is_refund(rows):
    assert (rows[4].kind, rows[4].amount_cents) == ("expense", -4500)


def test_comma_in_description_and_large_amount(rows):
    r = rows[5]
    assert (r.raw_description, r.amount_cents) == ("FAKE STORE, INC REF #900000000000006", 150000)
    assert r.raw_row["Description"] == "FAKE STORE, INC REF #900000000000006"
    assert r.raw_row["Amount"] == "-$1,500.00"


def test_interest_is_spending(rows):
    assert (rows[6].kind, rows[6].amount_cents) == ("expense", 321)


def test_rewards_excluded(rows):
    assert (rows[7].kind, rows[7].reason) == ("excluded", "rewards redemption")


def test_blank_rows_skipped(rows):
    assert len(rows) == 8


def test_other_formats_rejected_both_ways():
    from budget.parsers import chase, citi
    for other in ("citi", "chase"):
        with pytest.raises(ValueError, match="BCU"):
            bcu.parse((FIXTURES / other / "sample.csv").read_text())
    with pytest.raises(ValueError, match="Citi"):
        citi.parse(FIXTURE.read_text())
    with pytest.raises(ValueError, match="Chase"):
        chase.parse(FIXTURE.read_text())


def test_bad_row_reports_line_number():
    header = FIXTURE.read_text().splitlines()[0]
    with pytest.raises(ValueError, match="Line 2"):
        bcu.parse(header + '\nA,B,13/45/26,,X,,,,"-$1.00",\n')
