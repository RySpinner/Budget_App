"""Monthly budgets per top-level category.

A budget row applies from its effective_from month onward, until a later
row for the same category takes over. Changing a budget therefore adds a
row "from month X" and never rewrites earlier months.
"""

from dataclasses import dataclass
from datetime import date

import psycopg


@dataclass
class BudgetRow:
    id: int
    category_id: int
    category: str
    monthly_amount_cents: int
    effective_from: date


def first_of_month(day: date) -> date:
    return day.replace(day=1)


def set_budget(conn: psycopg.Connection, top_category_id: int, from_month: date, amount_cents: int) -> None:
    """Set a category's monthly budget from `from_month` onward (replaces a row for that same month)."""
    if amount_cents < 0:
        raise ValueError("A budget can't be negative")
    with conn.transaction():
        conn.execute(
            """
            INSERT INTO budgets (category_id, monthly_amount_cents, effective_from)
            VALUES (%s, %s, %s)
            ON CONFLICT (category_id, effective_from)
            DO UPDATE SET monthly_amount_cents = EXCLUDED.monthly_amount_cents
            """,
            (top_category_id, amount_cents, first_of_month(from_month)),
        )


def delete_budget(conn: psycopg.Connection, budget_id: int) -> None:
    with conn.transaction():
        conn.execute("DELETE FROM budgets WHERE id = %s", (budget_id,))


def budget_history(conn: psycopg.Connection) -> list[BudgetRow]:
    rows = conn.execute(
        """
        SELECT b.id, b.category_id, c.name, b.monthly_amount_cents, b.effective_from
          FROM budgets b JOIN categories c ON c.id = b.category_id
         ORDER BY c.sort_order, c.name, b.effective_from DESC
        """
    ).fetchall()
    return [BudgetRow(*r) for r in rows]
