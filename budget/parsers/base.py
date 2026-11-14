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
    "excluded"  not stored at all: only pending rows, non-transactions and
                points-only rows. Anything that posts in real currency is
                stored (SPEC section 2, principle 3).
    None        unknown: rules or a person decide later (Review). On a
                credit card, the importer treats unknown as "expense",
                since everything on a card that isn't a payment is a
                purchase, refund or fee. The exception is a rewards credit
                (reason REWARDS_CREDIT), which stays unknown everywhere:
                whether rewards are income or a negative expense is OPEN.

reason is stored with the transaction (transactions.parser_hint), so later
steps still know what the parser saw, e.g. "card payment" or "rewards credit".

balance_cents is the running balance as the institution states it, in the
account's own terms (money held in a checking account). The importer turns
the end-of-day balances into balance snapshots.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

Kind = Literal["expense", "income", "transfer", "excluded"] | None

REWARDS_CREDIT = "rewards credit"
POINTS_ONLY = "points only"


@dataclass(frozen=True)
class ParsedRow:
    kind: Kind
    txn_date: date | None
    posted_date: date | None
    amount_cents: int
    raw_description: str
    raw_row: dict = field(default_factory=dict)  # original CSV row, column -> text
    reason: str | None = None                    # why excluded (required then), or a hint such as "card payment"
    balance_cents: int | None = None             # the bank's running balance after this row, if the export has one

    def __post_init__(self):
        if self.txn_date is None and self.posted_date is None:
            raise ValueError(f"Row has no date: {self.raw_row}")
        if self.kind == "excluded" and not self.reason:
            raise ValueError("An excluded row needs a reason")


def rewards_row(amount_cents: int) -> tuple[Kind, str]:
    """(kind, reason) for a row whose description says it is rewards / cash back.

    A credit in real money (e.g. points redeemed as a statement credit) is
    stored and waits in Review. A row that moves no money (points only) is
    excluded. Points may be tracked later.
    """
    if amount_cents == 0:
        return "excluded", POINTS_ONLY
    return None, REWARDS_CREDIT


def describe(kind: str | None, amount_cents: int, reason: str | None = None) -> str:
    """Short label for screens: "purchase", "refund", "transfer (card payment)", "excluded: pending"..."""
    if kind == "expense":
        return "refund" if amount_cents < 0 else "purchase"
    if kind == "excluded":
        return f"excluded: {reason}"
    if kind == "transfer":
        return f"transfer ({reason})" if reason else "transfer"
    if kind is None and reason:
        return f"needs review ({reason})"
    return kind or "needs review"
