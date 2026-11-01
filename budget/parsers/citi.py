"""Parser for Citi credit card CSV exports.

Format (seen in real exports):
    Status,Date,Description,Debit,Credit
    Cleared,10/26/2026,FAKE MERCHANT 123 CT null XXXXXXXXXXXX0000,$38.64 ,
followed by several all-blank rows (",,,,"). Some exports end the header
with an extra comma, giving a blank sixth column.

Citi quotes the description but NOT the amount, so the thousands comma in a
large amount splits it across two columns. A real download (as seen in Notepad):
    Cleared,10/01/2026,"AUTOPAY ...AUTO-PMT",, -$ 1,878.69
The same file after being opened and saved in Excel looks different again:
    Cleared,9/1/2026,AUTOPAY ...AUTO-PMT,,($1),587.82
Both are handled. Each row is read from both ends (see _split_row): status and
date from the left, the amount from the right (re-joining thousands groups),
and everything in between is the description. Both versions of a file give the
same fingerprints, so importing both doesn't create duplicates.

- Date is MM/DD/YYYY. Citi gives only one date, which we treat as the
  transaction date (posted_date stays NULL).
- Charges are in Debit; payments and refunds are in Credit. Credits are made
  negative here whatever sign Citi writes them with.
- Status "Pending" rows are excluded; only cleared rows are recorded.
- Interest and fees count as spending (not excluded).
- The "XXXXXXXXXXXX1234" at the end of a description is usually a virtual card
  number that differs by merchant, NOT the card's real last 4, so it is not
  used to identify the card. It stays in raw_description; merchant cleanup
  strips it from the display name.
- TODO (SPEC section 11, "Credits"): payments are confirmed (AUTOPAY rows);
  the refund format is still unconfirmed.
"""

import csv
import io
import re
from datetime import datetime

from budget.money import parse_cents
from budget.parsers.base import ParsedRow, kind_for_amount

EXPECTED_COLUMNS = ["Status", "Date", "Description", "Debit", "Credit"]

# Credits that are not refunds: paying the card off, rewards redemptions.
EXCLUDE_PATTERNS = [
    (re.compile(r"\bAUTOPAY\b|\bAUTO-PMT\b", re.I), "card payment"),
    (re.compile(r"\bPAYMENT\b.*\bTHANK YOU\b", re.I), "card payment"),
    (re.compile(r"\bBALANCE TRANSFER\b", re.I), "balance transfer"),
    (re.compile(r"\b(CASH REWARD|REWARDS? REDEMPTION|THANKYOU POINTS)\b", re.I), "rewards redemption"),
]

# The start of a money amount that may continue in the next column, e.g. " -$ 1", "$2", "($1)".
AMOUNT_START = re.compile(r"^[\s(\-$]*\d{1,3}\)?\s*$")
# A thousands group that was split off by a comma, e.g. "878.69", "345.67 ", "587.82)".
THOUSANDS_GROUP = re.compile(r"^\d{3}(\.\d{1,2})?\)?\s*$")


def parse(text: str) -> list[ParsedRow]:
    reader = csv.reader(io.StringIO(text))
    header = [h.strip() for h in next(reader, [])]
    while header and header[-1] == "":
        header.pop()   # tolerate a trailing comma on the header line
    if header != EXPECTED_COLUMNS:
        raise ValueError(f"This doesn't look like a Citi export. Expected columns {EXPECTED_COLUMNS}, got {header}")

    rows = []
    for line_number, fields in enumerate(reader, start=2):
        if all(f.strip() == "" for f in fields):
            continue
        try:
            rows.append(_parse_row(fields))
        except ValueError as e:
            raise ValueError(f"Line {line_number}: {e}") from None
    return rows


def _split_row(fields: list[str]) -> tuple[str, str, str, str, str]:
    """Rebuild (status, date, description, debit, credit) from a row whose
    unquoted commas may have split the description or the amount.

    1. Drop empty fields at the end (unused Credit / extra columns).
    2. Take the amount from the right, gluing back thousands groups:
       ["($1)", "587.82"] -> "($1),587.82".
    3. If the field just before the amount is empty, that was the empty Debit,
       so the amount is a Credit; otherwise it is a Debit.
    4. Whatever lies between Date and the amount is the description (re-joined
       with commas if it was split).
    """
    t = list(fields)
    while t and t[-1].strip() == "":
        t.pop()
    if len(t) < 4:
        raise ValueError(f"Row has too few columns: {fields}")

    start = len(t) - 1
    while start > 3 and THOUSANDS_GROUP.match(t[start]) and AMOUNT_START.match(t[start - 1]):
        start -= 1
    amount = ",".join(t[start:])

    if t[start - 1].strip() == "":       # empty Debit column, so this is a Credit
        debit, credit = "", amount
        description = ",".join(t[2:start - 1])
    else:
        debit, credit = amount, ""
        description = ",".join(t[2:start])
    return t[0], t[1], description, debit, credit


def _amount_cents(text: str) -> int:
    """Parse an amount that may have been split and re-joined oddly, e.g.
    "($1),587.82" -> -158782. Parentheses or a minus sign mean negative."""
    negative = "(" in text or "-" in text
    digits = re.sub(r"[\s$(),\-]", "", text)
    if not re.fullmatch(r"\d+(\.\d{1,2})?", digits):
        raise ValueError(f"Not a money amount: {text!r}")
    cents = parse_cents(digits)
    return -cents if negative else cents


def _parse_row(fields: list[str]) -> ParsedRow:
    status, date_text, description, debit, credit = _split_row(fields)
    txn_date = datetime.strptime(date_text.strip(), "%m/%d/%Y").date()
    description = description.strip()

    if debit.strip():
        amount = _amount_cents(debit)
    elif credit.strip():
        amount = -abs(_amount_cents(credit))
    else:
        raise ValueError("Row has neither a Debit nor a Credit")

    reason = None
    if status.strip().lower() == "pending":
        reason = "pending"
    else:
        for pattern, why in EXCLUDE_PATTERNS:
            if pattern.search(description):
                reason = why
                break

    # Keep the original fields exactly as read (nothing re-joined), by column name;
    # anything past the five named columns is kept in order under "_extra".
    raw_row = dict(zip(EXPECTED_COLUMNS, fields))
    if len(fields) > len(EXPECTED_COLUMNS):
        raw_row["_extra"] = fields[len(EXPECTED_COLUMNS):]

    return ParsedRow(
        kind="excluded" if reason else kind_for_amount(amount),
        txn_date=txn_date,
        posted_date=None,
        amount_cents=amount,
        raw_description=description,
        raw_row=raw_row,
        exclude_reason=reason,
    )
