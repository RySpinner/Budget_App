import pytest

from budget.merchant import clean_merchant


@pytest.mark.parametrize("raw, expected", [
    ("FAKE AUTO PARTS 5550001234 CT null XXXXXXXXXXXX0000", "FAKE AUTO PARTS"),
    ("SQ *FAKE COFFEE ROASTERS SPRINGFIELD IL null XXXXXXXXXXXX0000", "FAKE COFFEE ROASTERS SPRINGFIELD"),
    ("TST* FAKE PIZZA #0123 SPRINGFIELD IL", "FAKE PIZZA SPRINGFIELD"),
    ("ONLINE PAYMENT; THANK YOU", "ONLINE PAYMENT; THANK YOU"),
    ("  lots   of   spaces  ", "lots of spaces"),
    ("12345", "12345"),  # never reduce a name to nothing
])
def test_clean_merchant(raw, expected):
    assert clean_merchant(raw) == expected


def test_reference_number_suffix_removed():
    assert clean_merchant("FAKE CAFE SPRINGFIELD IL REF #900000000000002") == "FAKE CAFE SPRINGFIELD"
    assert clean_merchant("Amazon.com*ASDF     Amzn.com/billWA REF #22222222222222222") == "Amazon.com*ASDF Amzn.com/billWA"


def test_store_number_glued_to_city_removed():
    assert clean_merchant("FAKE GROCER #123 000000SPRINGFIELD      IL") == "FAKE GROCER SPRINGFIELD"


def test_bank_statement_descriptions():
    ach = ("Deposit ACH FAKE EMPLOYER TYPE: DIR DEP ID: 0000000000 CO: FAKE EMPLOYER "
           "Entry Class Code: PPD ACH Trace Number: 0000000000000000")
    assert clean_merchant(ach) == "FAKE EMPLOYER"
    assert clean_merchant("Withdrawal FAKE GROCER") == "FAKE GROCER"
    assert clean_merchant("Withdrawal Home Banking Pmt to card XXXXXXXXXXXX-0000") == "Home Banking Pmt to card"
