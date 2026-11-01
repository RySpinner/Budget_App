"""Parser for Citi credit card CSV exports.

Format (seen in a real export):
    Status,Date,Description,Debit,Credit
    Cleared,10/26/2026,FCP EURO 8603889001 CT null XXXXXXXXXXXX7720,$38.64 ,
followed by several all-blank rows (",,,,").

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
- TODO (SPEC section 11, "Credits"): the payment patterns below are guesses;
  check them against real Citi payment and refund rows.
"""

import csv
import io
import re
from datetime import datetime

from budget.money import parse_cents
from budget.parsers.base import ParsedRow, is_blank_row, kind_for_amount

EXPECTED_COLUMNS = ["Status", "Date", "Description", "Debit", "Credit"]

# Credits that are not refunds: paying the card off, rewards redemptions.
EXCLUDE_PATTERNS = [
    (re.compile(r"\bAUTOPAY\b|\bAUTO-PMT\b", re.I), "card payment"),
    (re.compile(r"\bPAYMENT\b.*\bTHANK YOU\b", re.I), "card payment"),
    (re.compile(r"\bBALANCE TRANSFER\b", re.I), "balance transfer"),
    (re.compile(r"\b(CASH REWARD|REWARDS? REDEMPTION|THANKYOU POINTS)\b", re.I), "rewards redemption"),
]


def parse(text: str) -> list[ParsedRow]:
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip() for h in (reader.fieldnames or [])]
    if header != EXPECTED_COLUMNS:
        raise ValueError(f"This doesn't look like a Citi export. Expected columns {EXPECTED_COLUMNS}, got {header}")

    rows = []
    for line_number, raw in enumerate(reader, start=2):
        raw = {k.strip(): (v or "") for k, v in raw.items() if k is not None}
        if is_blank_row(raw):
            continue
        try:
            rows.append(_parse_row(raw))
        except ValueError as e:
            raise ValueError(f"Line {line_number}: {e}") from None
    return rows


def _parse_row(raw: dict) -> ParsedRow:
    txn_date = datetime.strptime(raw["Date"].strip(), "%m/%d/%Y").date()
    description = raw["Description"].strip()
    debit, credit = raw["Debit"].strip(), raw["Credit"].strip()

    if debit and credit:
        raise ValueError("Row has both a Debit and a Credit")
    if debit:
        amount = parse_cents(debit)
    elif credit:
        amount = -abs(parse_cents(credit))
    else:
        raise ValueError("Row has neither a Debit nor a Credit")

    reason = None
    if raw["Status"].strip().lower() == "pending":
        reason = "pending"
    else:
        for pattern, why in EXCLUDE_PATTERNS:
            if pattern.search(description):
                reason = why
                break

    return ParsedRow(
        kind="excluded" if reason else kind_for_amount(amount),
        txn_date=txn_date,
        posted_date=None,
        amount_cents=amount,
        raw_description=description,
        raw_row=raw,
        exclude_reason=reason,
    )
