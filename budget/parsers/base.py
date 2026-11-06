"""The shared shape every parser produces.

A parser takes the text of one CSV export and returns a list of ParsedRow,
one per data row (blank rows are skipped). Everything downstream works only
with ParsedRow, so it never needs to know which bank a row came from.

Sign convention for amount_cents (same as the database, SPEC section 3):
    positive = against the household (a purchase; money out of an asset
    account; more owed on a card), negative = the reverse (a refund, a
    deposit, a card payment received).
Each parser converts its bank's convention to this.

kind says what the row is, when the parser can tell:
    "expense"   a purchase, fee or interest (negative = a refund)
    "income"    money earned (paycheck, interest received)
    "transfer"  a move between two of our own accounts (e.g. a card payment)
    "excluded"  not stored at all (pending rows, non-transactions)
    None        unknown: rules or a person decide later (Review)
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

Kind = Literal["expense", "income", "transfer", "excluded"] | None


@dataclass(frozen=True)
class ParsedRow:
    kind: Kind
    txn_date: date | None
    posted_date: date | None
    amount_cents: int
    raw_description: str
    raw_row: dict = field(default_factory=dict)  # original CSV row, column -> text
    reason: str | None = None                    # why excluded (required then), or a hint such as "card payment"

    def __post_init__(self):
        if self.txn_date is None and self.posted_date is None:
            raise ValueError(f"Row has no date: {self.raw_row}")
        if self.kind == "excluded" and not self.reason:
            raise ValueError("An excluded row needs a reason")


def describe(kind: str | None, amount_cents: int, reason: str | None = None) -> str:
    """Short label for screens: "purchase", "refund", "transfer (card payment)", "excluded: pending"..."""
    if kind == "expense":
        return "refund" if amount_cents < 0 else "purchase"
    if kind == "excluded":
        return f"excluded: {reason}"
    if kind == "transfer":
        return f"transfer ({reason})" if reason else "transfer"
    return kind or "needs review"
