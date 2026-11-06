from datetime import date
from pathlib import Path

import pytest

from budget.parsers import chase, get_parser

FIXTURE = Path(__file__).parent / "fixtures" / "chase" / "sample.csv"


@pytest.fixture
def rows():
    return chase.parse(FIXTURE.read_text())


def test_registered():
    assert get_parser("chase") is chase.parse


def test_sale_sign_is_flipped_and_both_dates_kept(rows):
    r = rows[0]
    assert (r.kind, r.amount_cents) == ("expense", 888)
    assert (r.txn_date, r.posted_date) == (date(2026, 10, 29), date(2026, 10, 30))
    assert r.raw_description == "FAKE CAFE"
    assert r.raw_row["Category"] == "Food & Drink"     # Chase's category kept, not used


def test_identical_rows_both_parsed(rows):
    assert [r.amount_cents for r in rows[1:3]] == [1250, 1250]


def test_return_is_negative_expense(rows):
    assert (rows[3].kind, rows[3].amount_cents) == ("expense", -4500)


def test_payment_is_transfer(rows):
    assert (rows[4].kind, rows[4].reason, rows[4].amount_cents) == ("transfer", "card payment", -123456)


def test_interest_fee_is_spending(rows):
    assert (rows[5].kind, rows[5].amount_cents) == ("expense", 321)


def test_rewards_adjustment_excluded(rows):
    assert (rows[6].kind, rows[6].reason) == ("excluded", "rewards redemption")


def test_comma_in_description(rows):
    r = rows[7]
    assert (r.raw_description, r.amount_cents, r.kind) == ("FAKE STORE, INC", 150000, "expense")
    assert r.raw_row["_extra"] == [""]


def test_other_adjustment_by_sign():
    text = ("Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"
            "01/02/2026,01/03/2026,BALANCE ADJUSTMENT,,Adjustment,10.00,\n"
            "01/02/2026,01/03/2026,FEE REVERSAL,,Adjustment,-2.00,\n")
    assert [(r.kind, r.amount_cents) for r in chase.parse(text)] == [("expense", -1000), ("expense", 200)]


def test_citi_file_rejected_and_vice_versa():
    from budget.parsers import citi
    citi_text = (Path(__file__).parent / "fixtures" / "citi" / "sample.csv").read_text()
    with pytest.raises(ValueError, match="Chase"):
        chase.parse(citi_text)
    with pytest.raises(ValueError, match="Citi"):
        citi.parse(FIXTURE.read_text())


def test_bad_row_reports_line_number():
    text = "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n13/45/2026,01/01/2026,X,,Sale,-1.00,\n"
    with pytest.raises(ValueError, match="Line 2"):
        chase.parse(text)
