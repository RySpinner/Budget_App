"""Parser for the credit union's credit card CSV export.

Format (seen in a real export, as downloaded):
    Account ID,Transaction ID,Date,Name,Description,Check Number,Category,Tags,Amount,Balance
    3333333-444...,2026-10-16:222...:Amazon.c,10/16/26,,Amazon.com*ASDF ... REF #222...,,,,"-$100.00",

- One date, MM/DD/YY (two-digit year). Treated as the transaction date;
  posted_date stays NULL.
- The credit union's sign is the opposite of ours: charges are NEGATIVE
  ("-$100.00") and payments/refunds positive ("$100.00"). We flip it, so
  spending is positive. Amounts are quoted, so a thousands comma is safe.
- There is no Type or Status column, so the description decides:
  "PAYMENT - THANK YOU" -> transfer (card payment), rewards wording ->
  excluded (rewards are OPEN), everything else -> expense (a positive
  credit union amount becomes a negative expense, i.e. a refund).
- Name, Check Number, Category, Tags and Balance were empty for this card.
  They are kept in raw_row. Description is the raw description (Name is used
  only if Description is empty).
- Descriptions aren't quoted. If one contains a comma, the columns before
  Description (4) and after it (5) are fixed, so anything extra in the
  middle is re-joined into the description.
- Unconfirmed (see DECISIONS.md): how pending rows, refunds and rewards look.
  The Transaction ID is unique per row and the same column layout has a
  Check Number and Balance, so this looks like the credit union's general
  export format and may also serve its checking/savings accounts (phase 9).
"""

import csv
import io
import re
from datetime import datetime

from budget.money import parse_cents
from budget.parsers.base import ParsedRow

EXPECTED_COLUMNS = [
    "Account ID", "Transaction ID", "Date", "Name", "Description",
    "Check Number", "Category", "Tags", "Amount", "Balance",
]
BEFORE_DESCRIPTION = 4      # Account ID, Transaction ID, Date, Name
AFTER_DESCRIPTION = 5       # Check Number, Category, Tags, Amount, Balance

SPECIAL_ROWS = [
    (re.compile(r"\bPAYMENT\b.*\bTHANK YOU\b", re.I), "transfer", "card payment"),
    (re.compile(r"\b(REWARDS? REDEMPTION|REDEMPTION|CASH ?BACK)\b", re.I), "excluded", "rewards redemption"),
]


def parse(text: str) -> list[ParsedRow]:
    reader = csv.reader(io.StringIO(text))
    header = [h.strip() for h in next(reader, [])]
    while header and header[-1] == "":
        header.pop()
    if header != EXPECTED_COLUMNS:
        raise ValueError(
            f"This doesn't look like a credit union export. Expected columns {EXPECTED_COLUMNS}, got {header}"
        )

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
    end = len(fields) - AFTER_DESCRIPTION
    values = [*fields[:BEFORE_DESCRIPTION], ",".join(fields[BEFORE_DESCRIPTION:end]), *fields[end:]]
    return dict(zip(EXPECTED_COLUMNS, values))


def _parse_row(fields: list[str]) -> ParsedRow:
    col = _columns(fields)
    txn_date = datetime.strptime(col["Date"].strip(), "%m/%d/%y").date()
    description = col["Description"].strip() or col["Name"].strip()
    if not description:
        raise ValueError("Row has no description")
    if not col["Amount"].strip():
        raise ValueError("Row has no amount")
    amount = -parse_cents(col["Amount"])     # credit union: negative = charge; ours: positive = spending

    kind, reason = "expense", None           # purchases, refunds, interest, fees
    for pattern, special_kind, why in SPECIAL_ROWS:
        if pattern.search(description):
            kind, reason = special_kind, why
            break

    return ParsedRow(
        kind=kind,
        txn_date=txn_date,
        posted_date=None,
        amount_cents=amount,
        raw_description=description,
        raw_row=col,          # original fields by column name (a split description re-joined)
        reason=reason,
    )
