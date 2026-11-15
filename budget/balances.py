"""Account balances and reconciliation (SPEC sections 3, 4 and 7; phase 11a).

Balances are always computed, never stored:
    asset (checking, savings, cash): money held  = opening - sum(amounts)
    liability (credit card):         money owed  = opening + sum(amounts)
using the database function account_balance() (migrations/0009). A row
counts on its balance_date: the posted date if the bank gave one, otherwise
the transaction date, because that's what the bank's own balance follows.

The opening balance is the balance at the end of its date. Rows on or
before that day are history already included in it.

Reconciliation compares each balance the bank states (a balance snapshot:
from an import's balance column, a statement, or typed in) with the computed
balance on that day. A difference means a missing or duplicated row, or a
wrong opening balance. If it can't be explained, a balance adjustment (kind
'adjustment') closes it. That adjustment changes the balance but is never
spending, income or a transfer.

"Reconciled through" marks the last day an account was checked and agreed
with the bank. The app warns before changes that would alter balances on
or before it.
"""

from dataclasses import dataclass
from datetime import date

import psycopg

from budget.money import format_cents

# Account types offered when adding an account, and the class each belongs to.
# (brokerage, retirement and loan are reserved for later phases.)
ACCOUNT_TYPES = {
    "checking": "asset",
    "savings": "asset",
    "cash": "asset",
    "credit_card": "liability",
}
SNAPSHOT_SOURCES = ("statement", "manual")     # balances typed in by a person
ADJUSTMENT_TEXT = "Balance adjustment"


@dataclass
class AccountSummary:
    id: int
    display_name: str
    institution: str
    account_class: str
    account_type: str
    last4: str | None
    owner_id: int
    owner: str
    parser_key: str | None
    active: bool
    opening_balance_cents: int | None
    opening_balance_date: date | None
    reconciled_through: date | None
    balance_cents: int | None              # computed, as of the day asked for
    bank_date: date | None                 # the latest bank-stated balance
    bank_cents: int | None
    difference_cents: int | None           # bank - computed on bank_date
    reconciled_difference_cents: int | None   # same, on the reconciled-through day

    @property
    def status(self) -> str:
        """A short label; never color alone."""
        if self.reconciled_difference_cents:
            return "⚠ Changed since reconciled"
        if self.bank_date is None:
            return "No bank balance yet"
        if self.difference_cents == 0:
            return "✓ Matches the bank"
        if self.difference_cents is None:
            return "Bank balance is before the opening date"
        return f"⚠ Off by {format_cents(abs(self.difference_cents))}"


def account_summaries(conn: psycopg.Connection, as_of: date, include_inactive: bool = False) -> list[AccountSummary]:
    """Every account with its computed balance on `as_of` and its latest reconciliation."""
    rows = conn.execute(
        """
        SELECT a.id, a.display_name, a.institution, a.account_class, a.account_type, a.last4,
               a.owner_person_id, p.name, a.parser_key, a.active,
               a.opening_balance_cents, a.opening_balance_date, a.reconciled_through,
               account_balance(a.id, %(as_of)s),
               last.as_of_date, last.bank_cents, last.difference_cents,
               (SELECT r.difference_cents FROM reconciliation r
                 WHERE r.account_id = a.id AND r.as_of_date = a.reconciled_through
                 ORDER BY CASE r.source WHEN 'statement' THEN 0 WHEN 'manual' THEN 1 ELSE 2 END
                 LIMIT 1)
          FROM accounts a
          JOIN people p ON p.id = a.owner_person_id
          LEFT JOIN LATERAL (
                SELECT r.as_of_date, r.bank_cents, r.difference_cents
                  FROM reconciliation r
                 WHERE r.account_id = a.id
                 ORDER BY r.as_of_date DESC,
                          CASE r.source WHEN 'statement' THEN 0 WHEN 'manual' THEN 1 ELSE 2 END
                 LIMIT 1) last ON true
         WHERE a.active OR %(inactive)s
         ORDER BY a.account_class, a.display_name
        """,
        {"as_of": as_of, "inactive": include_inactive},
    ).fetchall()
    return [AccountSummary(*r) for r in rows]


def balance_on(conn: psycopg.Connection, account_id: int, as_of: date) -> int | None:
    return conn.execute("SELECT account_balance(%s, %s)", (account_id, as_of)).fetchone()[0]


@dataclass
class ReconciliationRow:
    snapshot_id: int
    as_of_date: date
    source: str                 # import, statement or manual
    bank_cents: int
    computed_cents: int | None  # None: before the opening date
    difference_cents: int | None


def reconciliation_history(conn: psycopg.Connection, account_id: int) -> list[ReconciliationRow]:
    """Every bank-stated balance for an account next to the computed one, newest first."""
    rows = conn.execute(
        """SELECT snapshot_id, as_of_date, source, bank_cents, computed_cents, difference_cents
             FROM reconciliation WHERE account_id = %s
            ORDER BY as_of_date DESC, snapshot_id DESC""",
        (account_id,),
    ).fetchall()
    return [ReconciliationRow(*r) for r in rows]


# ---------------------------------------------------------------------------
# Changes
# ---------------------------------------------------------------------------

def _account(conn: psycopg.Connection, account_id: int) -> tuple:
    row = conn.execute(
        "SELECT account_class, owner_person_id, reconciled_through, opening_balance_date FROM accounts WHERE id = %s",
        (account_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"No account {account_id}")
    return row


def adjustment_amount_cents(account_class: str, difference_cents: int) -> int:
    """The transaction amount that moves the computed balance by `difference_cents`.

    Uses the sign convention (positive = against the household): to raise the
    money held in an asset the amount is negative (like a deposit); to raise
    what's owed on a liability it is positive (like a charge).
    """
    return -difference_cents if account_class == "asset" else difference_cents


def add_adjustment(conn: psycopg.Connection, account_id: int, as_of: date, difference_cents: int,
                   note: str | None = None) -> int:
    """Record a balance adjustment so the computed balance on `as_of` moves by `difference_cents`.

    difference_cents is "bank minus computed", as the reconciliation shows it.
    Returns the new transaction id.
    """
    if difference_cents == 0:
        raise ValueError("There's no difference to adjust")
    account_class, owner_id, _, opening_date = _account(conn, account_id)
    if opening_date is not None and as_of <= opening_date:
        raise ValueError("An adjustment must be after the opening balance date; change the opening balance instead")
    return conn.execute(
        """
        INSERT INTO transactions
            (source, account_id, txn_date, amount_cents, raw_description, merchant,
             kind, kind_source, purchaser_id, note)
        VALUES ('manual', %s, %s, %s, %s, %s, 'adjustment', 'manual', %s, %s)
        RETURNING id
        """,
        (account_id, as_of, adjustment_amount_cents(account_class, difference_cents), ADJUSTMENT_TEXT,
         ADJUSTMENT_TEXT, owner_id, (note or "").strip() or None),
    ).fetchone()[0]


def save_snapshot(conn: psycopg.Connection, account_id: int, as_of: date, balance_cents: int,
                  source: str = "manual") -> None:
    """Record a balance the bank states (from a statement, or read off the website).

    Replaces an earlier one of the same source for that day.
    """
    if source not in SNAPSHOT_SOURCES:
        raise ValueError(f"source must be one of {SNAPSHOT_SOURCES}")
    _account(conn, account_id)
    conn.execute(
        """
        INSERT INTO balance_snapshots (account_id, as_of_date, balance_cents, source)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (account_id, as_of_date, source) DO UPDATE SET balance_cents = EXCLUDED.balance_cents
        """,
        (account_id, as_of, balance_cents, source),
    )


def delete_snapshot(conn: psycopg.Connection, snapshot_id: int) -> None:
    """Delete a balance typed in by a person. Imported balances go when their import is undone."""
    cur = conn.execute("DELETE FROM balance_snapshots WHERE id = %s AND source <> 'import'", (snapshot_id,))
    if cur.rowcount == 0:
        raise ValueError("Only balances entered by hand can be deleted here; undo the import for imported ones")


def set_opening_balance(conn: psycopg.Connection, account_id: int, balance_cents: int | None,
                        as_of: date | None) -> None:
    """Set (or clear, with None and None) the balance at the end of `as_of`."""
    if (balance_cents is None) != (as_of is None):
        raise ValueError("An opening balance needs both an amount and a date")
    _account(conn, account_id)
    conn.execute(
        "UPDATE accounts SET opening_balance_cents = %s, opening_balance_date = %s WHERE id = %s",
        (balance_cents, as_of, account_id),
    )


def set_reconciled_through(conn: psycopg.Connection, account_id: int, as_of: date | None) -> None:
    """Mark the account as agreeing with the bank up to `as_of` (or clear it with None).

    Only allowed on a day with a bank balance that the computed balance matches.
    """
    if as_of is not None:
        difference = conn.execute(
            """SELECT difference_cents FROM reconciliation WHERE account_id = %s AND as_of_date = %s
                ORDER BY CASE source WHEN 'statement' THEN 0 WHEN 'manual' THEN 1 ELSE 2 END LIMIT 1""",
            (account_id, as_of),
        ).fetchone()
        if difference is None or difference[0] != 0:
            raise ValueError("Mark an account reconciled only on a day where its balance matches the bank's")
    conn.execute("UPDATE accounts SET reconciled_through = %s WHERE id = %s", (as_of, account_id))


def earliest_snapshot(conn: psycopg.Connection, account_id: int) -> tuple[date, int] | None:
    """(date, balance) of the earliest bank-stated balance, e.g. to use as the opening balance."""
    return conn.execute(
        "SELECT as_of_date, balance_cents FROM balance_snapshots WHERE account_id = %s "
        "ORDER BY as_of_date, CASE source WHEN 'statement' THEN 0 WHEN 'manual' THEN 1 ELSE 2 END LIMIT 1",
        (account_id,),
    ).fetchone()


def save_account(conn: psycopg.Connection, *, account_id: int | None = None, display_name: str,
                 institution: str, account_type: str, owner_person_id: int, last4: str | None = None,
                 parser_key: str | None = None, active: bool = True) -> int:
    """Add an account (account_id None) or change one. Returns its id."""
    display_name, institution = display_name.strip(), institution.strip()
    if not display_name or not institution:
        raise ValueError("An account needs a name and an institution")
    if account_type not in ACCOUNT_TYPES:
        raise ValueError(f"Account type must be one of {list(ACCOUNT_TYPES)}")
    last4 = (last4 or "").strip() or None
    params = (display_name, institution, ACCOUNT_TYPES[account_type], account_type, last4, owner_person_id,
              parser_key or None, active)
    try:
        with conn.transaction():      # a savepoint, so a duplicate name leaves the connection usable
            return _save_account(conn, account_id, account_type, params)
    except psycopg.errors.UniqueViolation:
        raise ValueError(f"There's already an account called {display_name!r}") from None


def _save_account(conn: psycopg.Connection, account_id: int | None, account_type: str, params: tuple) -> int:
    if account_id is None:
        return conn.execute(
            """INSERT INTO accounts (display_name, institution, account_class, account_type, last4,
                                     owner_person_id, parser_key, active)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            params,
        ).fetchone()[0]
    if conn.execute("SELECT 1 FROM transactions WHERE account_id = %s LIMIT 1", (account_id,)).fetchone():
        old_class = conn.execute("SELECT account_class FROM accounts WHERE id = %s", (account_id,)).fetchone()[0]
        if old_class != ACCOUNT_TYPES[account_type]:
            raise ValueError("This account has transactions, so it can't switch between an asset "
                             "(checking, savings, cash) and a credit card")
    conn.execute(
        """UPDATE accounts SET display_name = %s, institution = %s, account_class = %s, account_type = %s,
                               last4 = %s, owner_person_id = %s, parser_key = %s, active = %s
            WHERE id = %s""",
        (*params, account_id),
    )
    return account_id


# ---------------------------------------------------------------------------
# Warnings about reconciled periods
# ---------------------------------------------------------------------------

def reconciled_through_if_affected(conn: psycopg.Connection, account_id: int, day: date) -> date | None:
    """The account's reconciled-through date if `day` is on or before it (a change there alters
    a reconciled balance), else None."""
    through = _account(conn, account_id)[2]
    return through if through is not None and day <= through else None


def reconciled_rows_in_batch(conn: psycopg.Connection, batch_id: int) -> tuple[int, date | None]:
    """How many rows of an import fall on or before their account's reconciled-through date, and that date."""
    return conn.execute(
        """
        SELECT count(*), max(a.reconciled_through)
          FROM transactions t JOIN accounts a ON a.id = t.account_id
         WHERE t.import_batch_id = %s AND a.reconciled_through IS NOT NULL
           AND t.balance_date <= a.reconciled_through
        """,
        (batch_id,),
    ).fetchone()
