"""Parser for Capital One credit card CSV exports. Parser key: "capital-one".

Format (seen in a sample export, as downloaded; CRLF line endings):
    Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit
    11/13/2026,11/14/2026,1234,FAKE STORE,Merchandise,50.00,

- Two dates, MM/DD/YYYY: Transaction Date (txn_date) and Posted Date (posted_date).
- Capital One splits the amount over two columns, and only one is filled:
    Debit  = a purchase, fee or interest  -> positive in our sign
    Credit = a payment, refund or reward  -> negative in our sign
  Amounts can be whole dollars ("50", "100.00"). A row with both filled is
  refused (it is almost certainly an unquoted thousands comma, e.g. 1,234.56,
  that split the field; none has been seen yet).
- Some rows end with an extra trailing comma; it is ignored. A comma inside
  a description (unquoted) is handled by joining the middle fields back.
- Card No. is the last 4 digits of the card; it is kept in raw_row only (the
  Import page picks the account by format, not by number).
- Capital One's Category column is kept in raw_row but not used: "Payment/Credit"
  covers payments, cash back and refunds alike, so the description decides:
    AUTOPAY / PYMT / "PAYMENT ... THANK YOU"  -> transfer (card payment)
    CASH BACK / REWARD(S)                     -> kind unknown, waits in Review
                                                 (income vs negative expense is
                                                 OPEN); a $0 row is excluded
    everything else                           -> expense (a Credit is a refund)
- Unconfirmed (only three sample rows seen): refunds, interest/fees, pending
  rows (assumed not to appear in the export) and the exact payment wording.
"""

import csv
import io
import re
from datetime import datetime

from budget.money import parse_cents
from budget.parsers.base import ParsedRow, rewards_row

EXPECTED_COLUMNS = [
    "Transaction Date", "Posted Date", "Card No.", "Description", "Category", "Debit", "Credit",
]

SPECIAL_ROWS = [
    (re.compile(r"\bAUTOPAY\b|\bPYMT\b|\bPAYMENT\b.*\bTHANK YOU\b", re.I), "transfer", "card payment"),
]
REWARDS = re.compile(r"\b(CASH ?BACK|REWARDS?|MILES? REDEMPTION)\b", re.I)


def parse(text: str) -> list[ParsedRow]:
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    header = [h.strip() for h in next(reader, [])]
    while header and header[-1] == "":
        header.pop()
    if header != EXPECTED_COLUMNS:
        raise ValueError(
            f"This doesn't look like a Capital One export. Expected columns {EXPECTED_COLUMNS}, got {header}"
        )

    rows = []
    for fields in reader:
        if all(f.strip() == "" for f in fields):
            continue
        try:
            rows.append(_parse_row(fields))
        except ValueError as e:
            raise ValueError(f"Line {reader.line_num}: {e}") from None
    return rows


def _parse_row(fields: list[str]) -> ParsedRow:
    fields = list(fields)
    # A stray trailing comma adds an empty 8th field. But an unquoted comma in
    # the description also makes 8 fields, so drop the last one only when the
    # Debit position holds an amount (or is empty), not a category name.
    if (len(fields) == len(EXPECTED_COLUMNS) + 1 and fields[-1].strip() == ""
            and re.fullmatch(r"[\d.]*", fields[5].strip())):
        fields.pop()
    if len(fields) < len(EXPECTED_COLUMNS) - 1:    # a missing last (Credit) field is tolerated
        raise ValueError(f"Row has too few columns: {fields}")
    if len(fields) == len(EXPECTED_COLUMNS) - 1:
        fields.append("")
    if len(fields) > len(EXPECTED_COLUMNS):
        # An unquoted comma in the description: read from both ends.
        extra = len(fields) - len(EXPECTED_COLUMNS)
        fields = fields[:3] + [",".join(fields[3:4 + extra])] + fields[4 + extra:]
    col = dict(zip(EXPECTED_COLUMNS, fields))

    txn_date = datetime.strptime(col["Transaction Date"].strip(), "%m/%d/%Y").date()
    posted_date = datetime.strptime(col["Posted Date"].strip(), "%m/%d/%Y").date()
    description = " ".join(col["Description"].split())
    if not description:
        raise ValueError("Row has no description")

    debit, credit = col["Debit"].strip(), col["Credit"].strip()
    if debit and credit:
        raise ValueError(f"Row has both a Debit and a Credit: {debit!r}, {credit!r}")
    if not debit and not credit:
        raise ValueError("Row has no amount")
    amount = parse_cents(debit) if debit else -parse_cents(credit)

    kind, reason = "expense", None           # purchases, refunds, interest, fees
    for pattern, special_kind, why in SPECIAL_ROWS:
        if pattern.search(description):
            kind, reason = special_kind, why
            break
    else:
        if REWARDS.search(description):
            kind, reason = rewards_row(amount)

    return ParsedRow(
        kind=kind,
        txn_date=txn_date,
        posted_date=posted_date,
        amount_cents=amount,
        raw_description=description,
        raw_row=col,
        reason=reason,
    )
