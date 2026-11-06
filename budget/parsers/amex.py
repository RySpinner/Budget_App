"""Parser for American Express card CSV exports.

Format (seen in a real export, as downloaded):
    Date,Description,Amount,Extended Details,Appears On Your Statement As,Address,City/State,Zip Code,Country,Reference,Category
    11/01/2026,FAKE GROCER #123 000000SPRINGFIELD      IL,100,"00000000000 FAKE GROCER
    GROCERY STORES",FAKE GROCER #123 ...,1 FAKE AVE,"SPRINGFIELD
    IL",00000,UNITED STATES,900000000000000004',Merchandise & Supplies-Groceries

- Fields are properly quoted, and several (Extended Details, Address,
  City/State) contain line breaks inside the quotes, so one row can span
  several lines of the file. The csv module handles that.
- One date, MM/DD/YYYY. Treated as the transaction date; posted_date stays NULL.
- Amex already uses our sign: charges are positive (20.94), payments and
  credits negative (-119.99). Amounts have no "$" or thousands comma, and
  whole dollars may have no cents ("100", "-10").
- Reference ends with a stray apostrophe (Amex's way of stopping Excel from
  mangling a long number). It is kept as-is in raw_row.
- No Type or Status column, so the description decides:
    "... PAYMENT - THANK YOU"             -> transfer (card payment)
    rewards wording (MEMBERSHIP REWARDS)  -> excluded (rewards are OPEN)
    everything else                       -> expense; a negative amount is a
                                             refund, which includes Amex's
                                             benefit statement credits such as
                                             "$120 Disney Streaming Credit" (see
                                             DECISIONS.md)
- Amex's own Category column is kept in raw_row but not used to categorize.
"""

import csv
import io
import re
from datetime import datetime

from budget.money import parse_cents
from budget.parsers.base import ParsedRow

EXPECTED_COLUMNS = [
    "Date", "Description", "Amount", "Extended Details", "Appears On Your Statement As",
    "Address", "City/State", "Zip Code", "Country", "Reference", "Category",
]

SPECIAL_ROWS = [
    (re.compile(r"\bPAYMENT\b.*\bTHANK YOU\b", re.I), "transfer", "card payment"),
    (re.compile(r"\b(MEMBERSHIP REWARDS|REWARDS? REDEMPTION|POINTS? REDEMPTION|CASH ?BACK)\b", re.I),
     "excluded", "rewards redemption"),
]


def parse(text: str) -> list[ParsedRow]:
    reader = csv.reader(io.StringIO(text))
    header = [h.strip() for h in next(reader, [])]
    while header and header[-1] == "":
        header.pop()
    if header != EXPECTED_COLUMNS:
        raise ValueError(f"This doesn't look like an Amex export. Expected columns {EXPECTED_COLUMNS}, got {header}")

    rows = []
    for fields in reader:
        if all(f.strip() == "" for f in fields):
            continue
        # A row can span several lines, so report the line it ends on.
        try:
            rows.append(_parse_row(fields))
        except ValueError as e:
            raise ValueError(f"Line {reader.line_num}: {e}") from None
    return rows


def _parse_row(fields: list[str]) -> ParsedRow:
    if len(fields) < len(EXPECTED_COLUMNS):
        raise ValueError(f"Row has too few columns: {fields}")
    col = dict(zip(EXPECTED_COLUMNS, fields))
    if len(fields) > len(EXPECTED_COLUMNS):
        col["_extra"] = fields[len(EXPECTED_COLUMNS):]

    txn_date = datetime.strptime(col["Date"].strip(), "%m/%d/%Y").date()
    description = " ".join(col["Description"].split())      # collapse any line breaks
    if not description:
        raise ValueError("Row has no description")
    amount = parse_cents(col["Amount"])                       # Amex already uses our sign

    kind, reason = "expense", None           # purchases, refunds, statement credits, interest, fees
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
        raw_row=col,
        reason=reason,
    )
