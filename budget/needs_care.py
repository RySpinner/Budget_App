"""Rows that need extra care (SPEC section 5a, PINNED).

ATM withdrawals, Venmo / Zelle / Cash App payments and paper checks are easy
to get wrong: the same kind of row can be spending, income, a transfer or
money between the partners. Until each gets its own design (phase 12), they:
  - are flagged with a reason (transactions.needs_care);
  - wait in Review's "Needs care" tab instead of the ordinary queue;
  - are never sorted by rules and never linked automatically as transfers.
A person decides each one by hand.

Recognised from the bank description only, so this works for every
institution. Rows the bank itself calls a transfer (a card payment) are not
flagged. An ATM *fee* is ordinary spending and isn't flagged either.
"""

import re

import psycopg

NEEDS_CARE = [
    (re.compile(r"\bATM\b(?!.*\bFEE\b)", re.I), "ATM / cash"),
    (re.compile(r"\b(VENMO|ZELLE|CASH ?APP)\b", re.I), "Venmo / Zelle"),
    (re.compile(r"\b(CHECK|CHK)\s*#?\s*\d+\b", re.I), "paper check"),
]


def needs_care_reason(raw_description: str, parser_kind: str | None = None) -> str | None:
    """Why this row needs care, or None if it's an ordinary row."""
    if parser_kind == "transfer":
        return None
    for pattern, reason in NEEDS_CARE:
        if pattern.search(raw_description):
            return reason
    return None


def flag_existing(conn: psycopg.Connection) -> int:
    """Flag rows imported before this check existed. Returns how many were flagged.

    Only rows nobody has decided yet: no category, not a transfer, not split,
    and not chosen by hand. Rows a rule already categorized are left alone.
    """
    rows = conn.execute(
        """
        SELECT id, raw_description, CASE WHEN kind_source = 'parser' THEN kind END
          FROM transactions t
         WHERE needs_care IS NULL
           AND category_id IS NULL
           AND (kind IS NULL OR kind IN ('expense', 'income'))
           AND kind_source IS DISTINCT FROM 'manual'
           AND NOT EXISTS (SELECT 1 FROM splits s WHERE s.transaction_id = t.id)
        """
    ).fetchall()
    flagged = 0
    for txn_id, raw, parser_kind in rows:
        reason = needs_care_reason(raw, parser_kind)
        if reason:
            conn.execute("UPDATE transactions SET needs_care = %s WHERE id = %s", (reason, txn_id))
            flagged += 1
    return flagged
