"""Parser for Chase credit card CSV exports.

Format (seen in a real export, as downloaded):
    Transaction Date,Post Date,Description,Category,Type,Amount,Memo
    10/29/2026,10/30/2026,FAKE CAFE,Food & Drink,Sale,-8.88,

- Two dates: Transaction Date -> txn_date, Post Date -> posted_date.
- Chase's sign is the opposite of ours: charges are NEGATIVE (-8.88) and
  payments/returns are positive. We flip it, so spending is positive.
- The Type column says what a row is, so we use it rather than guessing from
  the description:
    Sale        -> expense (a purchase)
    Return      -> expense with a negative amount (a refund)
    Payment     -> transfer (the card's leg of a payment from another account)
    Fee         -> expense (interest and fees count as spending)
    Adjustment  -> expense (purchase or refund by sign), unless it looks like a
                   rewards redemption, which is excluded (rewards are OPEN)
  "Sale" and "Payment" are confirmed with real rows; the others follow Chase's
  documented export (see DECISIONS.md).
- Chase's own Category column ("Food & Drink") is kept in raw_row but not used
  to categorize; our rules do that.
- Fields aren't quoted. If a description contains a comma it would split; the
  last four columns are fixed (Category, Type, Amount, Memo), so anything
  extra in the middle is re-joined into the description.
"""

import csv
import io
import re
from datetime import datetime

from budget.money import parse_cents
from budget.parsers.base import ParsedRow

EXPECTED_COLUMNS = ["Transaction Date", "Post Date", "Description", "Category", "Type", "Amount", "Memo"]

REWARDS = re.compile(r"\b(REDEMPTION|REDEEM|CASH ?BACK|REWARDS?)\b", re.I)


def parse(text: str) -> list[ParsedRow]:
    reader = csv.reader(io.StringIO(text))
    header = [h.strip() for h in next(reader, [])]
    while header and header[-1] == "":
        header.pop()
    if header != EXPECTED_COLUMNS:
        raise ValueError(f"This doesn't look like a Chase export. Expected columns {EXPECTED_COLUMNS}, got {header}")

    rows = []
    for line_number, fields in enumerate(reader, start=2):
        if all(f.strip() == "" for f in fields):
            continue
        try:
            rows.append(_parse_row(fields))
        except ValueError as e:
            raise ValueError(f"Line {line_number}: {e}") from None
    return rows


def _columns(fields: list[str]) -> dict:
    """Map fields to column names, re-joining a description split by commas."""
    if len(fields) < len(EXPECTED_COLUMNS):
        raise ValueError(f"Row has too few columns: {fields}")
    extra = len(fields) - len(EXPECTED_COLUMNS)
    description = ",".join(fields[2:3 + extra])
    return dict(zip(EXPECTED_COLUMNS, [fields[0], fields[1], description, *fields[3 + extra:]]))


def _parse_row(fields: list[str]) -> ParsedRow:
    col = _columns(fields)
    txn_date = datetime.strptime(col["Transaction Date"].strip(), "%m/%d/%Y").date()
    posted_date = datetime.strptime(col["Post Date"].strip(), "%m/%d/%Y").date()
    description = col["Description"].strip()
    row_type = col["Type"].strip().lower()
    amount = -parse_cents(col["Amount"])        # Chase: negative = charge; ours: positive = spending

    kind, reason = "expense", None          # Sale, Return, Fee, other Adjustments
    if row_type == "payment":
        kind, reason = "transfer", "card payment"
    elif row_type == "adjustment" and REWARDS.search(description):
        kind, reason = "excluded", "rewards redemption"

    raw_row = dict(zip(EXPECTED_COLUMNS, fields))
    if len(fields) > len(EXPECTED_COLUMNS):
        raw_row["_extra"] = fields[len(EXPECTED_COLUMNS):]

    return ParsedRow(
        kind=kind,
        txn_date=txn_date,
        posted_date=posted_date,
        amount_cents=amount,
        raw_description=description,
        raw_row=raw_row,
        reason=reason,
    )
