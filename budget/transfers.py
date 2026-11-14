"""Transfer matching (SPEC section 6a).

A transfer is money moving between two of our own accounts, e.g. paying the
Citi card from checking. Each account's export has its own row for it (a
"leg"). Linking the two legs (the transfers table) says "these are one
movement", so neither counts as spending or income. The database checks that
a transfer has exactly two legs, in different accounts, summing to zero.

Linking happens three ways:
  - auto_match() links legs that can only belong together: a transfer row
    in one account and exactly one row with the opposite amount in another
    account within MATCH_WINDOW_DAYS, where that row in turn has no other
    candidate. A transfer rule's "other account" (counterpart) narrows the
    candidates. At least one side must already be a transfer; the other may
    be a transfer or a row of unknown kind (which then becomes a transfer,
    kind_source 'match'). Two rows of unknown kind are never linked
    automatically: that could just be a coincidence of amounts.
  - For an account with no CSV (Cash, a loan, value-only investments) there
    is never an imported row to find, so the app creates the other leg itself
    (generate_leg). The matcher does this when a transfer rule names such an
    account as the counterpart; a person can do it from Review.
  - A person links two rows by hand in Review (link with matched_by="manual").

Never linked automatically: rows flagged "needs care" (ATM, Venmo/Zelle,
checks), and rows whose link a person undid (no_auto_match).

Undoing a link (dissolve) deletes any generated leg. A row that only the
matcher had made a transfer goes back to unknown (a database trigger, see
migrations/0008_transfer_matching.sql).
"""

from dataclasses import dataclass
from datetime import date

import psycopg

MATCH_WINDOW_DAYS = 5      # SPEC 6a default (OPEN): how far apart two legs' dates may be
MANUAL_WINDOW_DAYS = 10    # Review suggests candidates up to this far apart


@dataclass(frozen=True)
class Candidate:
    """A row that could be the other leg of a transfer."""
    id: int
    effective_date: date
    account_id: int
    account: str
    amount_cents: int
    merchant: str | None
    kind: str | None


def candidates(conn: psycopg.Connection, transaction_id: int, *, manual: bool = False) -> list[Candidate]:
    """Rows that could be the other leg of this one, closest date first.

    Always: another account, the exact opposite amount, not linked yet, not
    split, not a generated leg.
    Automatic matching (manual=False) also needs: within MATCH_WINDOW_DAYS;
    kind transfer or unknown; not "needs care"; not unlinked by a person
    before; and each side's rule counterpart (if any) pointing at the other.
    For a person (manual=True): within MANUAL_WINDOW_DAYS, any kind.
    """
    auto_only = """
           AND (o.kind = 'transfer' OR o.kind IS NULL)
           AND NOT o.no_auto_match
           AND o.needs_care IS NULL
           AND (tr.counterpart_account_id IS NULL OR tr.counterpart_account_id = o.account_id)
           AND (orule.counterpart_account_id IS NULL OR orule.counterpart_account_id = t.account_id)
    """
    rows = conn.execute(
        f"""
        SELECT o.id, o.effective_date, o.account_id, a.display_name, o.amount_cents, o.merchant, o.kind
          FROM transactions t
          JOIN transactions o ON o.account_id <> t.account_id
                             AND o.amount_cents = -t.amount_cents
                             AND abs(o.effective_date - t.effective_date) <= %(window)s
          JOIN accounts a       ON a.id = o.account_id
          LEFT JOIN rules tr    ON tr.id = t.rule_id
          LEFT JOIN rules orule ON orule.id = o.rule_id
         WHERE t.id = %(id)s
           AND o.transfer_id IS NULL
           AND o.generated_from_id IS NULL
           AND NOT EXISTS (SELECT 1 FROM splits s WHERE s.transaction_id = o.id)
           {"" if manual else auto_only}
         ORDER BY abs(o.effective_date - t.effective_date), o.id
        """,
        {"id": transaction_id, "window": MANUAL_WINDOW_DAYS if manual else MATCH_WINDOW_DAYS},
    ).fetchall()
    return [Candidate(*r) for r in rows]


def auto_match(conn: psycopg.Connection) -> int:
    """Link every unmatched transfer leg that has exactly one possible partner.

    Also creates the other leg for transfer rules whose counterpart is an
    account with no CSV. Returns the number of transfers linked.
    """
    linked = 0
    with conn.transaction():
        legs = conn.execute(
            """
            SELECT t.id, r.counterpart_account_id, (ca.parser_key IS NULL AND ca.active)
              FROM transactions t
              LEFT JOIN rules r     ON r.id = t.rule_id
              LEFT JOIN accounts ca ON ca.id = r.counterpart_account_id
             WHERE t.kind = 'transfer'
               AND t.transfer_id IS NULL
               AND t.generated_from_id IS NULL
               AND NOT t.no_auto_match
               AND t.needs_care IS NULL
             ORDER BY t.effective_date, t.id
            """
        ).fetchall()
        for leg_id, hint, hint_has_no_csv in legs:
            if _is_linked(conn, leg_id):      # linked earlier in this loop, as someone's partner
                continue
            if hint is not None and hint_has_no_csv:
                generate_leg(conn, leg_id, hint, matched_by="rule")
                linked += 1
                continue
            found = candidates(conn, leg_id)
            if len(found) != 1:
                continue                      # none, or ambiguous: a person decides in Review
            other = found[0].id
            if [c.id for c in candidates(conn, other)] != [leg_id]:
                continue                      # the partner has other options too
            link(conn, leg_id, other, matched_by="rule" if hint is not None else "auto")
            linked += 1
    return linked


def _is_linked(conn: psycopg.Connection, transaction_id: int) -> bool:
    return conn.execute(
        "SELECT transfer_id IS NOT NULL FROM transactions WHERE id = %s", (transaction_id,)
    ).fetchone()[0]


def _leg(conn: psycopg.Connection, transaction_id: int) -> dict:
    row = conn.execute(
        """
        SELECT t.id, t.account_id, a.display_name, a.parser_key, t.amount_cents, t.effective_date,
               t.transfer_id, t.kind, t.purchaser_id, t.generated_from_id,
               EXISTS (SELECT 1 FROM splits s WHERE s.transaction_id = t.id)
          FROM transactions t JOIN accounts a ON a.id = t.account_id
         WHERE t.id = %s
        """,
        (transaction_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"No transaction {transaction_id}")
    keys = ("id", "account_id", "account", "parser_key", "amount_cents", "effective_date",
            "transfer_id", "kind", "purchaser_id", "generated_from_id", "has_splits")
    return dict(zip(keys, row))


def _check_linkable(leg: dict) -> None:
    if leg["transfer_id"] is not None:
        raise ValueError(f"Transaction {leg['id']} is already linked to a transfer")
    if leg["has_splits"]:
        raise ValueError(f"Transaction {leg['id']} is split; remove the split before linking it")
    if leg["generated_from_id"] is not None:
        raise ValueError(f"Transaction {leg['id']} was created as the other half of a transfer")


def _make_transfer_leg(conn: psycopg.Connection, leg: dict, transfer_id: int, kind_source: str) -> None:
    """Point a row at a transfer. A row that wasn't a transfer yet becomes one (its category is cleared)."""
    if leg["kind"] == "transfer":
        conn.execute("UPDATE transactions SET transfer_id = %s, no_auto_match = false WHERE id = %s",
                     (transfer_id, leg["id"]))
    else:
        conn.execute(
            """UPDATE transactions
                  SET kind = 'transfer', kind_source = %s,
                      category_id = NULL, category_source = NULL, rule_id = NULL,
                      transfer_id = %s, no_auto_match = false
                WHERE id = %s""",
            (kind_source, transfer_id, leg["id"]),
        )


def link(conn: psycopg.Connection, a_id: int, b_id: int, matched_by: str = "manual") -> int:
    """Link two rows as the legs of one transfer. Returns the transfer id.

    Rows that weren't transfers yet become transfers: by hand that counts as a
    manual choice; the matcher marks them 'match'.
    """
    a, b = _leg(conn, a_id), _leg(conn, b_id)
    for leg in (a, b):
        _check_linkable(leg)
    if a["account_id"] == b["account_id"]:
        raise ValueError("The two legs of a transfer must be in different accounts")
    if a["amount_cents"] + b["amount_cents"] != 0:
        raise ValueError("The two legs of a transfer must have opposite amounts")
    kind_source = "manual" if matched_by == "manual" else "match"
    with conn.transaction():
        transfer_id = conn.execute(
            "INSERT INTO transfers (matched_by) VALUES (%s) RETURNING id", (matched_by,)
        ).fetchone()[0]
        _make_transfer_leg(conn, a, transfer_id, kind_source)
        _make_transfer_leg(conn, b, transfer_id, kind_source)
    return transfer_id


def generate_leg(conn: psycopg.Connection, transaction_id: int, account_id: int,
                 matched_by: str = "manual") -> int:
    """Create the other leg of a transfer in an account that has no CSV, and link the two.

    The new row has the opposite amount and the same date. Its description
    says where the money came from or went ("From BCU Checking"). Returns
    the new row's id.
    """
    leg = _leg(conn, transaction_id)
    _check_linkable(leg)
    target = conn.execute("SELECT parser_key, active FROM accounts WHERE id = %s", (account_id,)).fetchone()
    if target is None:
        raise ValueError(f"No account {account_id}")
    if target[0] is not None:
        raise ValueError("That account has its own CSV; link to its row instead (import it first if needed)")
    if account_id == leg["account_id"]:
        raise ValueError("The two legs of a transfer must be in different accounts")

    # The leg's positive amount means money left that account (or more is owed on it), so it
    # arrived in the target: "From <account>". A negative amount went the other way.
    description = f"{'From' if leg['amount_cents'] > 0 else 'To'} {leg['account']}"
    kind_source = "manual" if matched_by == "manual" else "match"
    with conn.transaction():
        new_id = conn.execute(
            """
            INSERT INTO transactions
                (source, account_id, txn_date, amount_cents, raw_description, merchant,
                 kind, kind_source, purchaser_id, generated_from_id)
            VALUES ('manual', %s, %s, %s, %s, %s, 'transfer', 'manual', %s, %s)
            RETURNING id
            """,
            (account_id, leg["effective_date"], -leg["amount_cents"], description, description,
             leg["purchaser_id"], leg["id"]),
        ).fetchone()[0]
        transfer_id = conn.execute(
            "INSERT INTO transfers (matched_by) VALUES (%s) RETURNING id", (matched_by,)
        ).fetchone()[0]
        _make_transfer_leg(conn, leg, transfer_id, kind_source)
        conn.execute("UPDATE transactions SET transfer_id = %s WHERE id = %s", (transfer_id, new_id))
    return new_id


def dissolve(conn: psycopg.Connection, transfer_id: int | None) -> None:
    """Remove a transfer link. A generated leg is deleted; the other legs become unmatched."""
    if transfer_id is None:
        return
    with conn.transaction():
        conn.execute("DELETE FROM transactions WHERE transfer_id = %s AND generated_from_id IS NOT NULL",
                     (transfer_id,))
        conn.execute("DELETE FROM transfers WHERE id = %s", (transfer_id,))


def unlink_by_hand(conn: psycopg.Connection, transfer_id: int) -> None:
    """A person says two rows don't belong together: unlink them and don't re-link them automatically."""
    with conn.transaction():
        legs = [r[0] for r in conn.execute(
            "SELECT id FROM transactions WHERE transfer_id = %s AND generated_from_id IS NULL", (transfer_id,)
        )]
        dissolve(conn, transfer_id)
        conn.execute("UPDATE transactions SET no_auto_match = true WHERE id = ANY(%s)", (legs,))


# ---------------------------------------------------------------------------
# Lists for the Review page
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UnmatchedLeg:
    id: int
    effective_date: date
    amount_cents: int
    account_id: int
    account: str
    merchant: str | None
    raw_description: str
    counterpart_account_id: int | None   # from the transfer rule, if any


def unmatched_legs(conn: psycopg.Connection) -> list[UnmatchedLeg]:
    """Transfer rows still waiting for their other leg, newest first."""
    rows = conn.execute(
        """
        SELECT t.id, t.effective_date, t.amount_cents, t.account_id, a.display_name, t.merchant,
               t.raw_description, r.counterpart_account_id
          FROM transactions t
          JOIN accounts a    ON a.id = t.account_id
          LEFT JOIN rules r  ON r.id = t.rule_id
         WHERE t.kind = 'transfer' AND t.transfer_id IS NULL AND t.generated_from_id IS NULL
         ORDER BY t.effective_date DESC, t.id
        """
    ).fetchall()
    return [UnmatchedLeg(*r) for r in rows]


@dataclass(frozen=True)
class LinkedTransfer:
    transfer_id: int
    matched_by: str
    effective_date: date
    from_account: str          # where the money left (the leg with the positive amount)
    to_account: str
    amount_cents: int          # how much moved (positive)
    description: str           # the "from" leg's merchant


def recent_links(conn: psycopg.Connection, since: date) -> list[LinkedTransfer]:
    """Linked transfers whose legs fall on or after `since`, newest first."""
    rows = conn.execute(
        """
        SELECT tr.id, tr.matched_by, least(o.effective_date, i.effective_date),
               oa.display_name, ia.display_name, o.amount_cents, COALESCE(o.merchant, o.raw_description)
          FROM transfers tr
          JOIN transactions o ON o.transfer_id = tr.id AND o.amount_cents > 0
          JOIN transactions i ON i.transfer_id = tr.id AND i.amount_cents < 0
          JOIN accounts oa ON oa.id = o.account_id
          JOIN accounts ia ON ia.id = i.account_id
         WHERE greatest(o.effective_date, i.effective_date) >= %s
         ORDER BY 3 DESC, tr.id DESC
        """,
        (since,),
    ).fetchall()
    return [LinkedTransfer(*r) for r in rows]


def accounts_without_csv(conn: psycopg.Connection) -> dict[int, str]:
    """Active accounts with no parser (e.g. Cash): their transfer legs are created by the app."""
    return dict(conn.execute(
        "SELECT id, display_name FROM accounts WHERE active AND parser_key IS NULL ORDER BY display_name"
    ).fetchall())
