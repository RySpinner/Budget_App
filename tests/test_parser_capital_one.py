from datetime import date
from pathlib import Path

import pytest

from budget.parsers import PARSERS, capital_one, get_parser

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = FIXTURES / "capital-one" / "sample.csv"
HEADER = "Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit"


@pytest.fixture
def rows():
    return capital_one.parse(FIXTURE.read_text(encoding="utf-8-sig"))


def test_registered():
    assert get_parser("capital-one") is capital_one.parse


def test_blank_rows_skipped(rows):
    assert len(rows) == 8


def test_purchase_is_positive_with_both_dates(rows):
    r = rows[0]
    assert (r.kind, r.amount_cents) == ("expense", 888)
    assert (r.txn_date, r.posted_date) == (date(2026, 11, 10), date(2026, 11, 12))


def test_identical_rows_both_parsed(rows):
    assert [r.amount_cents for r in rows[:2]] == [888, 888]


def test_credit_is_refund_and_comma_in_quoted_description(rows):
    r = rows[2]
    assert (r.raw_description, r.kind, r.amount_cents) == ("FAKE OUTDOOR STORE, INC", "expense", -4500)


def test_large_amount_and_whole_dollars(rows):
    assert rows[3].amount_cents == 150000
    assert rows[4].amount_cents == 5000


def test_interest_is_expense(rows):
    assert (rows[5].kind, rows[5].amount_cents) == ("expense", 321)


def test_cash_back_stored_for_review(rows):
    assert (rows[6].kind, rows[6].reason, rows[6].amount_cents) == (None, "rewards credit", -2500)


def test_autopay_is_transfer_despite_trailing_comma(rows):
    r = rows[7]
    assert (r.kind, r.reason, r.amount_cents) == ("transfer", "card payment", -123456)


def test_raw_row_keeps_original_text(rows):
    assert rows[7].raw_row["Credit"] == "1234.56"
    assert rows[7].raw_row["Category"] == "Payment/Credit"
    assert rows[7].raw_row["Card No."] == "0000"


def test_unquoted_comma_in_description():
    text = HEADER + "\n11/01/2026,11/02/2026,0000,FAKE SHOP, LLC,Merchandise,5.00,\n"
    r = capital_one.parse(text)[0]
    assert (r.raw_description, r.amount_cents) == ("FAKE SHOP, LLC", 500)


def test_debit_and_credit_both_filled_is_refused():
    text = HEADER + "\n11/01/2026,11/02/2026,0000,FAKE,Merchandise,1,234.56\n"
    with pytest.raises(ValueError, match="Line 2"):
        capital_one.parse(text)


def test_other_formats_rejected_both_ways():
    for key in ("citi", "chase", "bcu", "amex"):
        with pytest.raises(ValueError, match="Capital One"):
            capital_one.parse((FIXTURES / key / "sample.csv").read_text(encoding="utf-8-sig"))
        with pytest.raises(ValueError):
            PARSERS[key](FIXTURE.read_text())


def test_bad_date_reports_line_number():
    with pytest.raises(ValueError, match="Line 2"):
        capital_one.parse(HEADER + "\n13/45/2026,11/02/2026,0000,X,Y,1.00,\n")
