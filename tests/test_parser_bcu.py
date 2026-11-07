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
    assert (r.kind, r.amount_cents) == (None, 888)     # the importer makes unknown card rows expenses
    assert r.raw_description == "FAKE CAFE SPRINGFIELD IL REF #900000000000002"
    assert r.raw_row["Transaction ID"] == "2026-10-30:900000000000002:FAKE CAF"


def test_identical_rows_both_parsed(rows):
    assert [r.amount_cents for r in rows[2:4]] == [1250, 1250]


def test_positive_non_payment_is_unknown_with_negative_amount(rows):
    assert (rows[4].kind, rows[4].amount_cents) == (None, -4500)     # a refund, once on a card


def test_comma_in_description_and_large_amount(rows):
    r = rows[5]
    assert (r.raw_description, r.amount_cents) == ("FAKE STORE, INC REF #900000000000006", 150000)
    assert r.raw_row["Description"] == "FAKE STORE, INC REF #900000000000006"
    assert r.raw_row["Amount"] == "-$1,500.00"


def test_interest_amount(rows):
    assert (rows[6].kind, rows[6].amount_cents) == (None, 321)


def test_rewards_excluded(rows):
    assert (rows[7].kind, rows[7].reason) == ("excluded", "rewards redemption")


def test_blank_rows_skipped(rows):
    assert len(rows) == 8


def test_card_has_no_balance(rows):
    assert all(r.balance_cents is None for r in rows)


@pytest.fixture
def checking():
    return credit_union_checking()


def credit_union_checking():
    return bcu.parse((FIXTURES / "bcu" / "checking.csv").read_text())


def test_checking_direct_deposit_is_income_and_money_in_is_negative(checking):
    r = checking[0]
    assert (r.kind, r.reason, r.amount_cents, r.balance_cents) == ("income", "direct deposit", -200000, 300000)
    assert r.txn_date == date(2026, 11, 4)


def test_checking_card_payment_is_transfer(checking):
    r = checking[1]
    assert (r.kind, r.reason, r.amount_cents, r.balance_cents) == ("transfer", "card payment", 20000, 100000)


def test_checking_other_rows_are_unknown(checking):
    assert [(r.kind, r.amount_cents) for r in checking[2:]] == [
        (None, 5000), (None, -25000), (None, 888), (None, 888),
    ]


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
