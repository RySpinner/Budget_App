from datetime import date
from pathlib import Path

import pytest

from budget.parsers import get_parser, sofi

FIXTURE = Path(__file__).parent / "fixtures" / "sofi" / "checking.csv"


@pytest.fixture
def rows():
    return sofi.parse(FIXTURE.read_text())


def test_registered():
    assert get_parser("sofi") is sofi.parse


def test_blank_rows_skipped(rows):
    assert len(rows) == 7


def test_pending_excluded(rows):
    r = rows[0]
    assert (r.kind, r.reason, r.amount_cents) == ("excluded", "pending", 1200)


def test_card_payment_is_transfer(rows):
    r = rows[1]
    assert (r.kind, r.reason, r.amount_cents, r.balance_cents) == ("transfer", "card payment", 20000, 186224)
    assert r.txn_date == date(2026, 11, 5)
    assert r.posted_date is None


def test_direct_deposit_is_income_with_unquoted_thousands(rows):
    r = rows[2]
    assert (r.kind, r.reason, r.amount_cents, r.balance_cents) == ("income", "direct deposit", -200000, 206224)
    assert r.raw_row["Amount"] == "2,000.00"
    assert r.raw_row["Current balance"] == "2,062.24"
    assert r.raw_description == "FAKE EMPLOYER PAYROLL"


def test_comma_in_description(rows):
    r = rows[3]
    assert (r.raw_description, r.kind, r.amount_cents) == ("FAKE STORE, INC", None, 4500)
    assert r.raw_row["Type"] == "DEBIT_CARD"


def test_identical_rows_both_parsed(rows):
    assert [(r.amount_cents, r.balance_cents) for r in rows[4:6]] == [(888, 10724), (888, 11612)]


def test_bonus_is_unknown_money_in(rows):
    r = rows[6]
    assert (r.kind, r.amount_cents, r.balance_cents) == (None, -2500, 12500)
    assert r.raw_row == {
        "Date": "2026-11-01", "Description": "Money Welcome Bonus", "Type": "BONUS",
        "Amount": "25", "Current balance": "125", "Status": "Posted",
    }


def test_ambiguous_amounts_refused():
    with pytest.raises(ValueError, match="Line 2.*amount from the balance"):
        sofi.parse("Date,Description,Type,Amount,Current balance,Status\n"
                   "2026-11-01,X,ACH,-1,000,000,Posted\n")


def test_bad_row_reports_line_number():
    with pytest.raises(ValueError, match="Line 2"):
        sofi.parse("Date,Description,Type,Amount,Current balance,Status\n13/45/2026,X,ACH,-1,5,Posted\n")


def test_old_month_day_year_dates_still_read():
    header = "Date,Description,Type,Amount,Current balance,Status\n"
    [iso] = sofi.parse(header + "2026-11-13,X,ACH,-1,5,Posted\n")
    [mdy] = sofi.parse(header + "11/13/2026,X,ACH,-1,5,Posted\n")
    assert iso.txn_date == mdy.txn_date == date(2026, 11, 13)


def test_bad_iso_date_reports_line_number():
    with pytest.raises(ValueError, match="Line 2.*Not a date"):
        sofi.parse("Date,Description,Type,Amount,Current balance,Status\n2026-13-45,X,ACH,-1,5,Posted\n")
