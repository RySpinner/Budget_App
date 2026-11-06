"""Reading and editing transactions for the Transactions page.

The page shows transaction *lines* (see the transaction_lines view in
migrations/0004_accounts.sql): an unsplit transaction is one line, a split
transaction is one line per split. Each line is identified by
(transaction_id, split_id), where split_id is None for an unsplit transaction.

Every transaction has a kind: expense (a refund is a negative expense),
income, transfer (between two of our own accounts), or None (not known yet).
Choosing a category sets the kind to match it; choosing TRANSFER makes the
row a transfer.

Running monthly totals and budget-to-date are computed here when the page
asks for them; they are never stored.
"""

from dataclasses import dataclass
from datetime import date

import psycopg
from psycopg.rows import dict_row

UNCATEGORIZED = -1        # category filter value: lines with no category
TRANSFER = "transfer"     # "category" choice meaning: a transfer between our accounts
UNKNOWN_KIND = "unknown"  # kind filter value: rows whose kind isn't known yet
KINDS = ("expense", "income", "transfer", UNKNOWN_KIND)


@dataclass
class Filters:
    month: date | None = None             # first day of a month, or None for all
    kind: str | None = "expense"          # one of KINDS, or None for every kind
    top_category_id: int | None = None    # a top-level category, UNCATEGORIZED, or None for all
    category_id: int | None = None        # a subcategory, or None for all
    account_id: int | None = None
    purchaser_id: int | None = None


@dataclass
class Line:
    transaction_id: int
    split_id: int | None
    split_count: int
    source: str
    kind: str | None
    transfer_id: int | None
    effective_date: date
    amount_cents: int
    category_id: int | None
    top_category_id: int | None
    merchant: str | None
    raw_description: str
    account_id: int
    account: str
    purchaser_id: int
    note: str | None
    running_total_cents: int | None = None    # filled in by add_running_totals
    budget_to_date_cents: int | None = None   # filled in when a top-level category is chosen


def months_with_data(conn: psycopg.Connection) -> list[date]:
    rows = conn.execute("SELECT DISTINCT month FROM transaction_lines ORDER BY month DESC").fetchall()
    return [r[0] for r in rows]


def cash_account_id(conn: psycopg.Connection) -> int:
    return conn.execute(
        "SELECT id FROM accounts WHERE account_type = 'cash' AND active ORDER BY id LIMIT 1"
    ).fetchone()[0]


def load_lines(conn: psycopg.Connection, f: Filters) -> list[Line]:
    """Transaction lines matching the filters, oldest first."""
    where, params = [], []
    if f.month is not None:
        where.append("month = %s")
        params.append(f.month)
    if f.kind == UNKNOWN_KIND:
        where.append("kind IS NULL")
    elif f.kind is not None:
        where.append("kind = %s")
        params.append(f.kind)
    if f.top_category_id == UNCATEGORIZED:
        where.append("category_id IS NULL")
    elif f.top_category_id is not None:
        where.append("top_category_id = %s")
        params.append(f.top_category_id)
    if f.category_id is not None:
        where.append("category_id = %s")
        params.append(f.category_id)
    if f.account_id is not None:
        where.append("account_id = %s")
        params.append(f.account_id)
    if f.purchaser_id is not None:
        where.append("purchaser_id = %s")
        params.append(f.purchaser_id)

    want_budget = f.top_category_id not in (None, UNCATEGORIZED)
    sql = f"""
        SELECT transaction_id, split_id, split_count, source, kind, transfer_id, effective_date, amount_cents,
               category_id, top_category_id, merchant, raw_description, account_id, account, purchaser_id, note,
               {"budget_to_date(top_category_id, effective_date)" if want_budget else "NULL"} AS budget_to_date_cents
          FROM transaction_lines
         {"WHERE " + " AND ".join(where) if where else ""}
         ORDER BY effective_date, transaction_id, split_id NULLS FIRST
    """
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(sql, params).fetchall()
    lines = [Line(**r) for r in rows]
    add_running_totals(lines)
    return lines


def add_running_totals(lines: list[Line]) -> None:
    """Running total of the given lines, restarting each month. Lines must be in date order."""
    total, current_month = 0, None
    for line in lines:
        month = line.effective_date.replace(day=1)
        if month != current_month:
            total, current_month = 0, month
        total += line.amount_cents
        line.running_total_cents = total


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------

def _dissolve_transfer(conn: psycopg.Connection, transaction_id: int) -> None:
    """Unlink a transfer this row belongs to; the other leg goes back to unmatched."""
    conn.execute(
        "DELETE FROM transfers WHERE id = (SELECT transfer_id FROM transactions WHERE id = %s)",
        (transaction_id,),
    )


def update_line(
    conn: psycopg.Connection,
    transaction_id: int,
    split_id: int | None,
    *,
    category_id: int | str | None,
    note: str | None,
    purchaser_id: int,
) -> None:
    """Save the editable fields of one line.

    category_id is a subcategory id, TRANSFER, or None (uncategorized).
    - A category sets the kind to match it (expense or income); a transfer
      link is dissolved first. Both count as manual choices, so rules won't
      change them.
    - TRANSFER makes the row a transfer between our accounts (no category).
    - Category and note belong to the split for a split line, otherwise to
      the transaction. Purchaser always belongs to the whole transaction.
    """
    note = (note or "").strip() or None
    with conn.transaction():
        current = conn.execute(
            "SELECT category_id, kind FROM transactions WHERE id = %s", (transaction_id,)
        ).fetchone()
        if current is None:
            raise ValueError(f"No transaction {transaction_id}")
        current_category, current_kind = current

        if split_id is not None:
            if category_id is None or category_id == TRANSFER:
                raise ValueError("A split line needs a category (remove the split to make it a transfer)")
            conn.execute(
                "UPDATE splits SET category_id = %s, note = %s WHERE id = %s AND transaction_id = %s",
                (category_id, note, split_id, transaction_id),
            )
        else:
            if category_id == TRANSFER:
                if current_kind != "transfer":
                    mark_as_transfer(conn, transaction_id)
            elif category_id is not None:
                if category_id != current_category or current_kind == "transfer":
                    _dissolve_transfer(conn, transaction_id)
                    conn.execute(
                        """UPDATE transactions
                              SET kind = (SELECT kind FROM categories WHERE id = %s), kind_source = 'manual',
                                  category_id = %s, category_source = 'manual', rule_id = NULL
                            WHERE id = %s""",
                        (category_id, category_id, transaction_id),
                    )
            elif current_category is not None:
                conn.execute(
                    "UPDATE transactions SET category_id = NULL, category_source = NULL, rule_id = NULL WHERE id = %s",
                    (transaction_id,),
                )
            conn.execute("UPDATE transactions SET note = %s WHERE id = %s", (note, transaction_id))
        conn.execute(
            "UPDATE transactions SET purchaser_id = %s WHERE id = %s AND purchaser_id <> %s",
            (purchaser_id, transaction_id, purchaser_id),
        )


def mark_as_transfer(conn: psycopg.Connection, transaction_id: int) -> None:
    """Make a row a transfer between our own accounts (by hand): no category, not spending.

    It stays unmatched until it is linked to the other account's leg (phase 10).
    """
    with conn.transaction():
        if conn.execute("SELECT 1 FROM splits WHERE transaction_id = %s", (transaction_id,)).fetchone():
            raise ValueError("Remove the split before marking this as a transfer")
        conn.execute(
            """UPDATE transactions
                  SET kind = 'transfer', kind_source = 'manual',
                      category_id = NULL, category_source = NULL, rule_id = NULL
                WHERE id = %s""",
            (transaction_id,),
        )


@dataclass
class SplitLine:
    amount_cents: int
    category_id: int
    note: str | None = None


def set_splits(conn: psycopg.Connection, transaction_id: int, lines: list[SplitLine]) -> None:
    """Replace a transaction's splits. An empty list removes splitting.

    The lines must add up exactly to the transaction amount. That is checked
    here first (for a clear message) and again by the database at commit.
    Only expense and income rows can be split, into categories of that kind.
    """
    row = conn.execute("SELECT amount_cents, kind FROM transactions WHERE id = %s", (transaction_id,)).fetchone()
    if row is None:
        raise ValueError(f"No transaction {transaction_id}")
    amount, kind = row
    if lines:
        if kind not in ("expense", "income"):
            raise ValueError("Only expense or income transactions can be split; set a category first")
        if len(lines) < 2:
            raise ValueError("A split needs at least two lines (otherwise just set the category)")
        total = sum(line.amount_cents for line in lines)
        if total != amount:
            raise ValueError(f"Split lines add up to {total} cents but the transaction is {amount} cents")

    with conn.transaction():
        conn.execute("DELETE FROM splits WHERE transaction_id = %s", (transaction_id,))
        for line in lines:
            conn.execute(
                "INSERT INTO splits (transaction_id, amount_cents, category_id, note) VALUES (%s, %s, %s, %s)",
                (transaction_id, line.amount_cents, line.category_id, (line.note or "").strip() or None),
            )


def get_splits(conn: psycopg.Connection, transaction_id: int) -> list[SplitLine]:
    rows = conn.execute(
        "SELECT amount_cents, category_id, note FROM splits WHERE transaction_id = %s ORDER BY id",
        (transaction_id,),
    ).fetchall()
    return [SplitLine(*r) for r in rows]


def add_manual(
    conn: psycopg.Connection,
    *,
    txn_date: date,
    amount_cents: int,
    description: str,
    purchaser_id: int,
    category_id: int | None = None,
    note: str | None = None,
    account_id: int | None = None,
) -> int:
    """Record something with no export (usually cash spending). Returns the new transaction id.

    Goes in the Cash account unless another account is given. The kind comes
    from the category; without one it is recorded as an expense awaiting a
    category in Review.
    """
    description = description.strip()
    if not description:
        raise ValueError("Description can't be empty")
    if amount_cents == 0:
        raise ValueError("Amount can't be zero")
    with conn.transaction():
        account_id = account_id or cash_account_id(conn)
        kind = "expense"
        if category_id is not None:
            kind = conn.execute("SELECT kind FROM categories WHERE id = %s", (category_id,)).fetchone()[0]
        return conn.execute(
            """
            INSERT INTO transactions
                (source, account_id, txn_date, amount_cents, raw_description, merchant,
                 kind, kind_source, category_id, category_source, purchaser_id, note)
            VALUES ('manual', %s, %s, %s, %s, %s, %s, 'manual', %s, %s, %s, %s)
            RETURNING id
            """,
            (account_id, txn_date, amount_cents, description, description, kind, category_id,
             "manual" if category_id is not None else None, purchaser_id, (note or "").strip() or None),
        ).fetchone()[0]


def delete_manual(conn: psycopg.Connection, transaction_id: int) -> None:
    """Delete a manual entry. Imported rows are removed by undoing their import instead."""
    with conn.transaction():
        cur = conn.execute("DELETE FROM transactions WHERE id = %s AND source = 'manual'", (transaction_id,))
        if cur.rowcount == 0:
            raise ValueError("Only manual entries can be deleted here; undo the import to remove imported rows")
