"""The shared shape every card parser produces.

A parser takes the text of one CSV export and returns a list of ParsedRow,
one per data row (blank rows are skipped). Everything downstream works only
with ParsedRow, so it never needs to know which bank a row came from.

Sign convention for amount_cents (same as the database):
    positive = spending, negative = refund/credit.
Each parser is responsible for converting its bank's convention to this.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

Kind = Literal["purchase", "refund", "excluded"]


@dataclass(frozen=True)
class ParsedRow:
    kind: Kind
    txn_date: date | None
    posted_date: date | None
    amount_cents: int
    raw_description: str
    raw_row: dict = field(default_factory=dict)  # original CSV row, column -> text
    exclude_reason: str | None = None            # set when kind == "excluded"

    def __post_init__(self):
        if self.txn_date is None and self.posted_date is None:
            raise ValueError(f"Row has no date: {self.raw_row}")
        if (self.kind == "excluded") != (self.exclude_reason is not None):
            raise ValueError("exclude_reason must be set exactly when kind is 'excluded'")


def kind_for_amount(amount_cents: int) -> Kind:
    return "refund" if amount_cents < 0 else "purchase"


def is_blank_row(row: dict) -> bool:
    return all((v or "").strip() == "" for v in row.values())
