from datetime import date
from pathlib import Path

import pytest

from budget.parsers import PARSERS, apple_card, get_parser

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = FIXTURES / "apple-card" / "sample.csv"
HEADER = "Transaction Date,Clearing Date,Description,Merchant,Category,Type,Amount (USD),Purchased By"


@pytest.fixture
def rows():
    return apple_card.parse(FIXTURE.read_text(encoding="utf-8-sig"))


def test_registered():
    assert get_parser("apple-card") is apple_card.parse


def test_blank_rows_skipped(rows):
    assert len(rows) == 7


def test_purchase_keeps_sign_and_both_dates(rows):
    r = rows[0]
    assert (r.kind, r.amount_cents) == ("expense", 999)
    assert (r.txn_date, r.posted_date) == (date(2026, 11, 10), date(2026, 11, 10))


def test_identical_rows_both_parsed(rows):
    assert [r.amount_cents for r in rows[1:3]] == [888, 888]
    assert rows[1].raw_row["Purchased By"] == "Person A"        # trailing space removed


def test_quoted_comma_and_thousands(rows):
    assert (rows[3].raw_description, rows[3].amount_cents) == ("FAKE OUTDOOR STORE, INC CITY ST", 150000)


def test_payment_is_transfer(rows):
    r = rows[4]
    assert (r.kind, r.reason, r.amount_cents) == ("transfer", "card payment", -123456)


def test_daily_cash_adjustment_waits_in_review_as_rewards(rows):
    r = rows[5]
    assert (r.kind, r.reason, r.amount_cents) == (None, "rewards credit", 200)
    assert r.txn_date == date(2026, 10, 5)                        # dates without leading zeros


def test_return_is_refund(rows):
    r = rows[6]
    assert (r.kind, r.amount_cents) == ("expense", -8948)
    assert (r.txn_date, r.posted_date) == (date(2026, 10, 3), date(2026, 10, 5))


def test_raw_row_keeps_original_text(rows):
    assert rows[6].raw_row["Type"] == "Credit"
    assert rows[6].raw_row["Amount (USD)"] == "-89.48"
    assert rows[6].raw_row["Merchant"] == "Fake Store (return)"


def test_unknown_type_is_unknown():
    r = apple_card.parse(HEADER + "\n11/01/2026,11/01/2026,X,X,Other,Mystery,1.00,A\n")[0]
    assert (r.kind, r.reason) == (None, None)


def test_wrong_column_count_refused():
    with pytest.raises(ValueError, match="Line 2"):
        apple_card.parse(HEADER + "\n11/01/2026,11/01/2026,X,X,Other,Purchase,1,234.56,A\n")


def test_other_formats_rejected_both_ways():
    for key in ("citi", "chase", "bcu", "amex"):
        with pytest.raises(ValueError, match="Apple Card"):
            apple_card.parse((FIXTURES / key / "sample.csv").read_text(encoding="utf-8-sig"))
        with pytest.raises(ValueError):
            PARSERS[key](FIXTURE.read_text())


def test_bad_date_reports_line_number():
    with pytest.raises(ValueError, match="Line 2"):
        apple_card.parse(HEADER + "\n13/45/2026,11/02/2026,X,X,Y,Purchase,1.00,A\n")
