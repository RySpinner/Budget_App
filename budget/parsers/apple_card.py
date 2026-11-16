"""Parser for Apple Card CSV exports. Parser key: "apple-card".

Format (seen in a sample export, as downloaded; CRLF line endings):
    Transaction Date,Clearing Date,Description,Merchant,Category,Type,Amount (USD),Purchased By
    11/10/2026,11/10/2026,FAKE SHOP CITY ST,Fake Shop,Shopping,Purchase,9.99,Person A

- Two dates, M/D/YYYY, with or without leading zeros ("10/5/2026"):
  Transaction Date is txn_date, Clearing Date is posted_date.
- Apple already uses our sign: a purchase is positive, a payment or return is
  negative. Amounts have no "$"; whole dollars and cents both appear.
- Description is the full bank text; Merchant is Apple's cleaned name and
  Category is Apple's own. Both are kept in raw_row but not used. Purchased By
  (which cardholder) is kept in raw_row too, with surrounding spaces removed.
- Type is Apple's own row type and decides the kind:
    Payment                       -> transfer (card payment)
    Purchase / Credit / Debit /
    Installment                   -> expense (a Credit, e.g. a return, is
                                     negative = a refund)
    anything else                 -> unknown (None); the importer treats it
                                     as an expense on a credit card
- DAILY CASH ADJUSTMENT (Type "Debit", a positive charge): when a purchase is
  returned, Apple pays the Daily Cash earned on it into the owner's savings /
  Apple Cash and then charges the same amount back to the card with this row.
  The original Daily Cash is NOT a row on the card. The adjustment is a
  reversal of a reward, so it is treated like a rewards row: kind unknown,
  reason "rewards credit", waiting in Review together with the cash-back
  deposit on the other account. Whatever is decided for rewards (income vs
  negative expense; DECISIONS.md, OPEN) is then applied to both, and the pair
  nets out. It is never a transfer and never counted as spending on its own.
- Unconfirmed (four sample rows seen): refunds are the "Credit" row only;
  interest, fees, installments and pending rows (assumed not exported);
  whether amounts of 1,000 or more are quoted. A row with a different number
  of columns than the header is refused rather than guessed at.
"""

import csv
import io
import re
from datetime import datetime

from budget.money import parse_cents
from budget.parsers.base import REWARDS_CREDIT, ParsedRow

EXPECTED_COLUMNS = [
    "Transaction Date", "Clearing Date", "Description", "Merchant", "Category",
    "Type", "Amount (USD)", "Purchased By",
]

SPECIAL_ROWS = [
    (re.compile(r"\bDAILY CASH ADJUSTMENT\b", re.I), None, REWARDS_CREDIT),
]
TYPE_KINDS = {
    "payment": ("transfer", "card payment"),
    "purchase": ("expense", None),
    "credit": ("expense", None),
    "debit": ("expense", None),
    "installment": ("expense", None),
}


def parse(text: str) -> list[ParsedRow]:
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    header = [h.strip() for h in next(reader, [])]
    while header and header[-1] == "":
        header.pop()
    if header != EXPECTED_COLUMNS:
        raise ValueError(
            f"This doesn't look like an Apple Card export. Expected columns {EXPECTED_COLUMNS}, got {header}"
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
    while len(fields) > len(EXPECTED_COLUMNS) and fields[-1].strip() == "":
        fields.pop()                               # stray trailing comma
    if len(fields) != len(EXPECTED_COLUMNS):
        raise ValueError(f"Expected {len(EXPECTED_COLUMNS)} columns, got {len(fields)}: {fields}")
    col = dict(zip(EXPECTED_COLUMNS, fields))
    col["Purchased By"] = col["Purchased By"].strip()

    txn_date = datetime.strptime(col["Transaction Date"].strip(), "%m/%d/%Y").date()
    posted_date = datetime.strptime(col["Clearing Date"].strip(), "%m/%d/%Y").date()
    description = " ".join(col["Description"].split())
    if not description:
        raise ValueError("Row has no description")
    amount = parse_cents(col["Amount (USD)"])      # Apple already uses our sign

    kind, reason = TYPE_KINDS.get(col["Type"].strip().lower(), (None, None))
    for pattern, special_kind, why in SPECIAL_ROWS:
        if pattern.search(description):
            kind, reason = special_kind, why
            break

    return ParsedRow(
        kind=kind,
        txn_date=txn_date,
        posted_date=posted_date,
        amount_cents=amount,
        raw_description=description,
        raw_row=col,
        reason=reason,
    )
