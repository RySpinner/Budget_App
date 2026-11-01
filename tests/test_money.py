import pytest

from budget.money import format_cents, parse_cents


@pytest.mark.parametrize("text, cents", [
    ("$38.64 ", 3864), ("1,023.99", 102399), ("-15.25", -1525), ("(15.25)", -1525),
    ("$-0.01", -1), ("12", 1200), ("0.10", 10),
])
def test_parse_cents(text, cents):
    assert parse_cents(text) == cents


@pytest.mark.parametrize("bad", ["", "abc", "1.005", "$"])
def test_parse_cents_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        parse_cents(bad)


def test_format_cents():
    assert format_cents(123456) == "$1,234.56"
    assert format_cents(-500) == "-$5.00"
    assert format_cents(7) == "$0.07"
