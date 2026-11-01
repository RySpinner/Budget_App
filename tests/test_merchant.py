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
