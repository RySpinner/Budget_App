"""Converting between bank text and integer cents. No floats anywhere."""

from decimal import Decimal, InvalidOperation


def parse_cents(text: str) -> int:
    """Turn bank text like "$1,234.56 ", "-25.00" or "(25.00)" into cents.

    Raises ValueError if the text isn't a money amount or has fractions of a cent.
    """
    cleaned = text.strip().replace("$", "").replace(",", "").replace(" ", "")
    negative = False
    if cleaned.startswith("(") and cleaned.endswith(")"):
        negative, cleaned = True, cleaned[1:-1]
    if cleaned.startswith("-"):
        negative, cleaned = not negative, cleaned[1:]
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"Not a money amount: {text!r}") from None
    cents = value * 100
    if cents != cents.to_integral_value():
        raise ValueError(f"Amount has fractions of a cent: {text!r}")
    return -int(cents) if negative else int(cents)


def format_cents(cents: int) -> str:
    """12345 -> "$123.45", -500 -> "-$5.00"."""
    sign = "-" if cents < 0 else ""
    dollars, rem = divmod(abs(cents), 100)
    return f"{sign}${dollars:,}.{rem:02d}"
