"""Parser for BCU (credit union) CSV exports, credit card and checking. Parser key: "bcu".

BCU uses one export format for every account (seen in real exports, as downloaded):
    Account ID,Transaction ID,Date,Name,Description,Check Number,Category,Tags,Amount,Balance
    card:     3333333-444...,2026-10-16:222...:Amazon.c,10/16/26,,Amazon.com*ASDF ... REF #222...,,,,"-$100.00",
    checking: 3333333-444...,20261104:111...,11/04/26,,Deposit ACH MY JOB TYPE: DIR DEP ...,,,,"$2000.00","$3,000.00"

- One date, MM/DD/YY (two-digit year). Treated as the transaction date;
  posted_date stays NULL.
- BCU's sign is the opposite of ours on both kinds of account: money out
  (a card charge, a checking withdrawal) is NEGATIVE, money in is positive.
  We flip it, so money out is positive. Amounts are quoted, so a thousands
  comma is safe.
- Balance: empty for the card; for checking it is the running balance after
  each row, kept as balance_cents (as stated: money held is positive).
- There is no Type or Status column, so the description decides:
    "PAYMENT - THANK YOU"        -> transfer (card payment, the card's side)
    "Pmt to card"                -> transfer (card payment, the checking side)
    "DIR DEP" (direct deposit)   -> income (usually a paycheck)
    rewards wording              -> excluded (rewards are OPEN)
    anything else                -> unknown (None). On the card the importer
                                    makes that an expense (a purchase, or a
                                    refund if negative); in checking it goes
                                    to Review, where rules or a person decide.
- Name, Check Number, Category and Tags were empty in both samples. They are
  kept in raw_row. Description is the raw description (Name is used only if
  Description is empty).
- Descriptions aren't quoted. If one contains a comma, the columns before
  Description (4) and after it (5) are fixed, so anything extra in the
  middle is re-joined into the description.
- Unconfirmed (see DECISIONS.md): how pending rows, refunds, rewards, debit
  card purchases, transfers to savings and ATM withdrawals look.
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
    (re.compile(r"\bPMT TO CARD\b", re.I), "transfer", "card payment"),
    (re.compile(r"\bDIR DEP\b", re.I), "income", "direct deposit"),
    (re.compile(r"\b(REWARDS? REDEMPTION|REDEMPTION|CASH ?BACK)\b", re.I), "excluded", "rewards redemption"),
]


def parse(text: str) -> list[ParsedRow]:
    reader = csv.reader(io.StringIO(text))
    header = [h.strip() for h in next(reader, [])]
    while header and header[-1] == "":
        header.pop()
    if header != EXPECTED_COLUMNS:
        raise ValueError(
            f"This doesn't look like a BCU export. Expected columns {EXPECTED_COLUMNS}, got {header}"
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
    amount = -parse_cents(col["Amount"])     # BCU: negative = money out; ours: positive = money out
    balance = parse_cents(col["Balance"]) if col["Balance"].strip() else None

    kind, reason = None, None                # unknown: see the module docstring
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
        balance_cents=balance,
    )
