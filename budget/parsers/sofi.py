"""Parser for SoFi checking CSV exports. Parser key: "sofi".

The format (seen in a real export, as downloaded):
    Date,Description,Type,Amount,Current balance,Status
    2026-06-30,FAKE MEMBERSHIP,DIRECT_PAY,-10,15,Posted

- One date, YYYY-MM-DD (e.g. 2026-11-13). Treated as the transaction date;
  posted_date stays NULL. Rows are newest first. M/D/YYYY (e.g. 11/13/2026,
  as in the first sample, or a copy saved from Excel) is accepted too; the
  same day gives the same fingerprint either way.
- SoFi's sign is the opposite of ours: money out is NEGATIVE, money in is
  positive. We flip it, so money out is positive.
- Amounts can be whole dollars ("-10", "25"); parse_cents handles that.
- Current balance is the running balance after each row, kept as
  balance_cents (as stated: money held is positive).
- Status: "Posted" rows are imported. Anything else (e.g. "Pending") is
  excluded, since a pending row may change or vanish and then import twice.
- Type is SoFi's own row type, in UPPER_SNAKE_CASE. It decides the kind:
    DIRECT_DEPOSIT              -> income (direct deposit)   [guess: not seen yet]
    anything else               -> unknown (None): Review, where rules or a
                                   person decide. Seen so far: DIRECT_PAY (a
                                   debit, e.g. the SoFi Plus fee) and BONUS
                                   (a welcome bonus).
  SPECIAL_ROWS then catch card payments by description, as for BCU.
- Nothing in the sample is quoted. If SoFi leaves a thousands comma
  unquoted ("1,000"), or a description contains a comma, the row has extra
  fields. It is then read from both ends: Date is first; Status is last;
  just before it are Amount and Current balance, possibly split at their
  thousands commas; the field before those is Type; everything between Date
  and Type is the description, re-joined.
- Unconfirmed (see DECISIONS.md): the Type values for debit card purchases,
  transfers, ATM withdrawals, direct deposits and interest; how pending rows
  and amounts of $1,000 or more look.
"""

import csv
import io
import re
from datetime import date, datetime

from budget.money import parse_cents
from budget.parsers.base import ParsedRow

EXPECTED_COLUMNS = ["Date", "Description", "Type", "Amount", "Current balance", "Status"]

TYPE_KINDS = {
    "DIRECT_DEPOSIT": ("income", "direct deposit"),
}

SPECIAL_ROWS = [
    (re.compile(r"\bPAYMENT\b.*\bTHANK YOU\b", re.I), "transfer", "card payment"),
    (re.compile(r"\bPMT TO CARD\b", re.I), "transfer", "card payment"),
]

FIRST_PIECE = re.compile(r"^\s*-?\$?\s*\d{1,3}(\.\d+)?\s*$")     # "-1" of "-1,000.00"
NEXT_PIECE = re.compile(r"^\d{3}(\.\d+)?\s*$")                  # "000.00" of "-1,000.00"

DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y")     # SoFi's format first; then M/D/YYYY (older sample / Excel)


def parse(text: str) -> list[ParsedRow]:
    reader = csv.reader(io.StringIO(text))
    header = [h.strip() for h in next(reader, [])]
    while header and header[-1] == "":
        header.pop()
    if header != EXPECTED_COLUMNS:
        raise ValueError(
            f"This doesn't look like a SoFi export. Expected columns {EXPECTED_COLUMNS}, got {header}"
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
    """Map fields to column names, re-joining a split description or amounts."""
    if len(fields) < len(EXPECTED_COLUMNS):
        raise ValueError(f"Row has too few columns: {fields}")
    if len(fields) == len(EXPECTED_COLUMNS):
        return dict(zip(EXPECTED_COLUMNS, fields))

    # Walk back from just before Status over the money pieces; the first
    # field that isn't one is Type.
    type_index = len(fields) - 2
    while type_index > 1 and (FIRST_PIECE.match(fields[type_index]) or NEXT_PIECE.match(fields[type_index])):
        type_index -= 1
    amount, balance = _two_amounts(fields[type_index + 1:-1])
    return {
        "Date": fields[0],
        "Description": ",".join(fields[1:type_index]),
        "Type": fields[type_index],
        "Amount": amount,
        "Current balance": balance,
        "Status": fields[-1],
    }


def _two_amounts(pieces: list[str]) -> tuple[str, str]:
    """Split pieces like ["-1", "000", "2", "500.00"] into "-1,000" and "2,500.00".

    Tries every split point and keeps the one where both halves are a
    properly written amount. Refuses to guess if there isn't exactly one.
    """
    def is_amount(part: list[str]) -> bool:
        return bool(part) and bool(FIRST_PIECE.match(part[0])) and all(NEXT_PIECE.match(p) for p in part[1:]) \
            and all("." not in p for p in part[:-1])

    splits = [i for i in range(1, len(pieces)) if is_amount(pieces[:i]) and is_amount(pieces[i:])]
    if len(splits) != 1:
        raise ValueError(f"Can't tell the amount from the balance in {','.join(pieces)!r}")
    i = splits[0]
    return ",".join(pieces[:i]), ",".join(pieces[i:])


def _parse_date(text: str) -> date:
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Not a date in YYYY-MM-DD (or M/D/YYYY) form: {text!r}")


def _parse_row(fields: list[str]) -> ParsedRow:
    col = _columns(fields)
    txn_date = _parse_date(col["Date"])
    description = col["Description"].strip()
    if not description:
        raise ValueError("Row has no description")
    if not col["Amount"].strip():
        raise ValueError("Row has no amount")
    amount = -parse_cents(col["Amount"])     # SoFi: negative = money out; ours: positive = money out
    balance = parse_cents(col["Current balance"]) if col["Current balance"].strip() else None

    status = col["Status"].strip()
    row_type = col["Type"].strip().upper()
    kind, reason = TYPE_KINDS.get(row_type, (None, None))     # unknown: see the module docstring
    for pattern, special_kind, why in SPECIAL_ROWS:
        if pattern.search(description):
            kind, reason = special_kind, why
            break
    if status.lower() != "posted":
        kind, reason = "excluded", "pending" if status.lower() == "pending" else f"not posted ({status or 'no status'})"

    return ParsedRow(
        kind=kind,
        txn_date=txn_date,
        posted_date=None,
        amount_cents=amount,
        raw_description=description,
        raw_row=col,          # original fields by column name (split fields re-joined)
        reason=reason,
        balance_cents=balance,
    )
