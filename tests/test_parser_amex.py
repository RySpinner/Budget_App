from datetime import date
from pathlib import Path

import pytest

from budget.parsers import amex, get_parser

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = FIXTURES / "amex" / "sample.csv"


@pytest.fixture
def rows():
    return amex.parse(FIXTURE.read_text())


def test_registered():
    assert get_parser("amex") is amex.parse


def test_all_rows_parsed_despite_line_breaks_inside_fields(rows):
    assert len(rows) == 10


def test_payment_is_transfer(rows):
    r = rows[0]
    assert (r.kind, r.reason, r.amount_cents) == ("transfer", "card payment", -123456)
    assert (r.txn_date, r.posted_date) == (date(2026, 11, 3), None)
    assert r.raw_row["Reference"] == "900000000000000001'"     # kept exactly, apostrophe and all


def test_statement_credit_is_refund(rows):
    assert (rows[1].kind, rows[1].amount_cents) == ("expense", -1000)     # whole dollars, no cents


def test_purchase_keeps_sign_and_multiline_fields(rows):
    r = rows[2]
    assert (r.kind, r.amount_cents) == ("expense", 2094)
    assert r.raw_row["City/State"] == "SPRINGFIELD\nIL"
    assert r.raw_row["Category"] == "Communications-Cable & Internet Comm"   # kept, not used


def test_identical_rows_both_parsed(rows):
    assert [r.amount_cents for r in rows[4:6]] == [888, 888]


def test_refund_with_quoted_comma(rows):
    assert (rows[6].raw_description, rows[6].kind, rows[6].amount_cents) == ("FAKE OUTDOOR STORE, INC", "expense", -4500)


def test_large_amount_and_interest(rows):
    assert rows[7].amount_cents == 150000
    assert (rows[8].kind, rows[8].amount_cents) == ("expense", 321)


def test_rewards_excluded(rows):
    assert (rows[9].kind, rows[9].reason) == ("excluded", "rewards redemption")


def test_windows_line_endings():
    text = FIXTURE.read_text().replace("\n", "\r\n")
    assert [r.amount_cents for r in amex.parse(text)] == [r.amount_cents for r in amex.parse(FIXTURE.read_text())]


def test_other_formats_rejected_both_ways():
    from budget.parsers import PARSERS
    for key in ("citi", "chase", "credit_union"):
        with pytest.raises(ValueError, match="Amex"):
            amex.parse((FIXTURES / key / "sample.csv").read_text())
        with pytest.raises(ValueError):
            PARSERS[key](FIXTURE.read_text())


def test_bad_row_reports_line_number():
    header = FIXTURE.read_text().splitlines()[0]
    with pytest.raises(ValueError, match="Line 2"):
        amex.parse(header + "\n13/45/2026,X,1.00,,,,,,,1',\n")
