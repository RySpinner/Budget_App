"""Reading and editing transactions for the Transactions page.

The page shows *spending lines* (see migrations/0002_spending_lines.sql):
an unsplit transaction is one line, a split transaction is one line per
split. Each line is identified by (transaction_id, split_id), where
split_id is None for an unsplit transaction.

Running monthly totals and budget-to-date are computed here when the page
asks for them; they are never stored.
"""

from dataclasses import dataclass
from datetime import date

import psycopg
from psycopg.rows import dict_row

UNCATEGORIZED = -1        # filter value: lines with no category
CASH = -1                 # filter value: manual entries (no card)


@dataclass
class Filters:
    month: date | None = None             # first day of a month, or None for all
    top_category_id: int | None = None    # a top-level category, UNCATEGORIZED, or None for all
    category_id: int | None = None        # a subcategory, or None for all
    card_id: int | None = None            # a card, CASH, or None for all
    purchaser_id: int | None = None


@dataclass
class Line:
    transaction_id: int
    split_id: int | None
    split_count: int
    source: str
    effective_date: date
    amount_cents: int
    category_id: int | None
    top_category_id: int | None
    merchant: str | None
    raw_description: str
    card: str | None
    purchaser_id: int
    note: str | None
    running_total_cents: int | None = None    # filled in by add_running_totals
    budget_to_date_cents: int | None = None   # filled in when a top-level category is chosen


def months_with_data(conn: psycopg.Connection) -> list[date]:
    rows = conn.execute("SELECT DISTINCT month FROM spending_lines ORDER BY month DESC").fetchall()
    return [r[0] for r in rows]


def load_lines(conn: psycopg.Connection, f: Filters) -> list[Line]:
    """Spending lines matching the filters, oldest first."""
    where, params = [], []
    if f.month is not None:
        where.append("month = %s")
        params.append(f.month)
    if f.top_category_id == UNCATEGORIZED:
        where.append("category_id IS NULL")
    elif f.top_category_id is not None:
        where.append("top_category_id = %s")
        params.append(f.top_category_id)
    if f.category_id is not None:
        where.append("category_id = %s")
        params.append(f.category_id)
    if f.card_id == CASH:
        where.append("card_id IS NULL")
    elif f.card_id is not None:
        where.append("card_id = %s")
        params.append(f.card_id)
    if f.purchaser_id is not None:
        where.append("purchaser_id = %s")
        params.append(f.purchaser_id)

    want_budget = f.top_category_id not in (None, UNCATEGORIZED)
    sql = f"""
        SELECT transaction_id, split_id, split_count, source, effective_date, amount_cents,
               category_id, top_category_id, merchant, raw_description, card, purchaser_id, note,
               {"budget_to_date(top_category_id, effective_date)" if want_budget else "NULL"} AS budget_to_date_cents
          FROM spending_lines
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

def update_line(
    conn: psycopg.Connection,
    transaction_id: int,
    split_id: int | None,
    *,
    category_id: int | None,
    note: str | None,
    purchaser_id: int,
) -> None:
    """Save the editable fields of one line.

    Category and note belong to the split for a split line, otherwise to the
    transaction. A category chosen here counts as a manual choice, so rules
    won't change it. Purchaser always belongs to the whole transaction.
    """
    note = (note or "").strip() or None
    with conn.transaction():
        if split_id is None:
            current = conn.execute(
                "SELECT category_id FROM transactions WHERE id = %s", (transaction_id,)
            ).fetchone()
            if current is None:
                raise ValueError(f"No transaction {transaction_id}")
            if current[0] != category_id:
                conn.execute(
                    """UPDATE transactions
                          SET category_id = %s, category_source = %s, rule_id = NULL
                        WHERE id = %s""",
                    (category_id, "manual" if category_id is not None else None, transaction_id),
                )
            conn.execute("UPDATE transactions SET note = %s WHERE id = %s", (note, transaction_id))
        else:
            if category_id is None:
                raise ValueError("A split line needs a category")
            conn.execute(
                "UPDATE splits SET category_id = %s, note = %s WHERE id = %s AND transaction_id = %s",
                (category_id, note, split_id, transaction_id),
            )
        conn.execute(
            "UPDATE transactions SET purchaser_id = %s WHERE id = %s AND purchaser_id <> %s",
            (purchaser_id, transaction_id, purchaser_id),
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
    """
    amount = conn.execute("SELECT amount_cents FROM transactions WHERE id = %s", (transaction_id,)).fetchone()
    if amount is None:
        raise ValueError(f"No transaction {transaction_id}")
    if lines:
        if len(lines) < 2:
            raise ValueError("A split needs at least two lines (otherwise just set the category)")
        total = sum(line.amount_cents for line in lines)
        if total != amount[0]:
            raise ValueError(f"Split lines add up to {total} cents but the transaction is {amount[0]} cents")

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
) -> int:
    """Record cash or other non-card spending. Returns the new transaction id."""
    description = description.strip()
    if not description:
        raise ValueError("Description can't be empty")
    if amount_cents == 0:
        raise ValueError("Amount can't be zero")
    with conn.transaction():
        return conn.execute(
            """
            INSERT INTO transactions
                (source, txn_date, amount_cents, raw_description, merchant,
                 category_id, category_source, purchaser_id, note)
            VALUES ('manual', %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (txn_date, amount_cents, description, description, category_id,
             "manual" if category_id is not None else None, purchaser_id, (note or "").strip() or None),
        ).fetchone()[0]


def delete_manual(conn: psycopg.Connection, transaction_id: int) -> None:
    """Delete a manual entry. Card transactions are removed by undoing their import instead."""
    with conn.transaction():
        cur = conn.execute("DELETE FROM transactions WHERE id = %s AND source = 'manual'", (transaction_id,))
        if cur.rowcount == 0:
            raise ValueError("Only manual entries can be deleted here; undo the import to remove card rows")
