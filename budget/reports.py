"""Numbers for the Dashboard. Everything is computed from the spending_lines
and monthly_totals views on request; nothing here is stored.

All amounts are integer cents.
"""

import calendar
from dataclasses import dataclass
from datetime import date

import psycopg


def month_end(month: date) -> date:
    return month.replace(day=calendar.monthrange(month.year, month.month)[1])


def as_of_day(month: date, today: date) -> date | None:
    """The day budget pace is measured at for a month.

    Current month -> today; past month -> its last day; future month -> None.
    """
    if month > today:
        return None
    if month.year == today.year and month.month == today.month:
        return today
    return month_end(month)


def add_months(month: date, n: int) -> date:
    index = month.year * 12 + month.month - 1 + n
    return date(index // 12, index % 12 + 1, 1)


@dataclass
class CategoryPace:
    top_category_id: int
    name: str
    spent_cents: int
    budget_cents: int | None      # monthly budget, None if not set
    pace_cents: int | None        # budget prorated to the as-of day

    @property
    def status(self) -> str:
        if self.budget_cents is None:
            return "No budget"
        if self.spent_cents > self.budget_cents:
            return "Over budget"
        if self.pace_cents is not None and self.spent_cents > self.pace_cents:
            return "Over pace"
        return "On pace"


def category_pace(conn: psycopg.Connection, month: date, as_of: date | None) -> list[CategoryPace]:
    """Spending vs budget for every active top-level category in a month."""
    rows = conn.execute(
        """
        SELECT c.id, c.name,
               COALESCE((SELECT sum(amount_cents) FROM monthly_totals m
                          WHERE m.month = %(month)s AND m.top_category_id = c.id), 0)::bigint,
               budget_for_month(c.id, %(month)s),
               CASE WHEN %(as_of)s::date IS NULL THEN NULL ELSE budget_to_date(c.id, %(as_of)s::date) END
          FROM categories c
         WHERE c.parent_id IS NULL AND c.active
         ORDER BY c.sort_order, c.name
        """,
        {"month": month, "as_of": as_of},
    ).fetchall()
    return [CategoryPace(*r) for r in rows]


def uncategorized_cents(conn: psycopg.Connection, month: date) -> int:
    return conn.execute(
        "SELECT COALESCE(sum(amount_cents), 0)::bigint FROM monthly_totals WHERE month = %s AND category_id IS NULL",
        (month,),
    ).fetchone()[0]


@dataclass
class TrendPoint:
    month: date
    top_category_id: int | None   # None = uncategorized
    amount_cents: int


def monthly_trend(conn: psycopg.Connection, last_month: date, months: int = 12) -> list[TrendPoint]:
    """Monthly spend per top-level category for `months` months ending at `last_month`."""
    first = add_months(last_month, -(months - 1))
    rows = conn.execute(
        """
        SELECT month, top_category_id, sum(amount_cents)::bigint
          FROM monthly_totals
         WHERE month BETWEEN %s AND %s
         GROUP BY month, top_category_id
         ORDER BY month, top_category_id NULLS LAST
        """,
        (first, last_month),
    ).fetchall()
    return [TrendPoint(*r) for r in rows]


@dataclass
class SubcategoryTotal:
    category_id: int
    name: str
    amount_cents: int


def subcategory_totals(conn: psycopg.Connection, month: date, top_category_id: int) -> list[SubcategoryTotal]:
    """Spend per subcategory of one top-level category in a month, largest first (zero rows left out)."""
    rows = conn.execute(
        """
        SELECT m.category_id, c.name, sum(m.amount_cents)::bigint AS total
          FROM monthly_totals m JOIN categories c ON c.id = m.category_id
         WHERE m.month = %s AND m.top_category_id = %s
         GROUP BY m.category_id, c.name
        HAVING sum(m.amount_cents) <> 0
         ORDER BY total DESC, c.name
        """,
        (month, top_category_id),
    ).fetchall()
    return [SubcategoryTotal(*r) for r in rows]


def category_trend(conn: psycopg.Connection, top_category_id: int, last_month: date, months: int = 12
                   ) -> list[tuple[date, int, int | None]]:
    """(month, spent_cents, budget_cents) for one category over `months` months, including empty months."""
    first = add_months(last_month, -(months - 1))
    return conn.execute(
        """
        SELECT g.month::date,
               COALESCE((SELECT sum(amount_cents) FROM monthly_totals m
                          WHERE m.month = g.month AND m.top_category_id = %(cat)s), 0)::bigint,
               budget_for_month(%(cat)s, g.month::date)
          FROM generate_series(%(first)s::date, %(last)s::date, interval '1 month') AS g(month)
         ORDER BY g.month
        """,
        {"cat": top_category_id, "first": first, "last": last_month},
    ).fetchall()
