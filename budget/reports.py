"""Numbers for the Dashboard. Everything is computed from the database views
(spending_lines, monthly_totals, transaction_lines) and the account_balance()
function on request; nothing here is stored.

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
    """Spending vs budget for every active top-level expense category in a month."""
    rows = conn.execute(
        """
        SELECT c.id, c.name,
               COALESCE((SELECT sum(amount_cents) FROM monthly_totals m
                          WHERE m.month = %(month)s AND m.top_category_id = c.id), 0)::bigint,
               budget_for_month(c.id, %(month)s),
               CASE WHEN %(as_of)s::date IS NULL THEN NULL ELSE budget_to_date(c.id, %(as_of)s::date) END
          FROM categories c
         WHERE c.parent_id IS NULL AND c.active AND c.kind = 'expense'
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


# ---------------------------------------------------------------------------
# Cash flow, net worth and the books check (phase 11b)
#
# Shown in household terms: money in and net worth going up are positive.
# (The database's sign convention is the reverse: positive = against us.)
# ---------------------------------------------------------------------------

@dataclass
class CashFlowMonth:
    month: date
    income_cents: int        # money earned (income rows, split-aware), positive
    spending_cents: int      # expense rows, refunds netted, uncategorized spending included
    unsorted_cents: int      # rows of unknown kind (Review), money in positive
    unsorted_count: int

    @property
    def saved_cents(self) -> int:
        """Income minus spending: positive = saved, negative = overspent. Transfers never count."""
        return self.income_cents - self.spending_cents


def cash_flow(conn: psycopg.Connection, last_month: date, months: int = 12) -> list[CashFlowMonth]:
    """Income vs spending per month (by effective_date, like budgets), including empty months.

    Transfers and balance adjustments are left out. Rows whose kind isn't
    known yet are reported separately, since they could be either.
    """
    first = add_months(last_month, -(months - 1))
    rows = conn.execute(
        """
        SELECT g.month::date,
               COALESCE((SELECT -sum(amount_cents) FROM transaction_lines l
                          WHERE l.month = g.month AND l.kind = 'income'), 0)::bigint,
               COALESCE((SELECT sum(amount_cents) FROM transaction_lines l
                          WHERE l.month = g.month AND l.kind = 'expense'), 0)::bigint,
               COALESCE((SELECT -sum(amount_cents) FROM transactions t
                          WHERE date_trunc('month', t.effective_date) = g.month AND t.kind IS NULL), 0)::bigint,
               (SELECT count(*) FROM transactions t
                 WHERE date_trunc('month', t.effective_date) = g.month AND t.kind IS NULL)
          FROM generate_series(%s::date, %s::date, interval '1 month') AS g(month)
         ORDER BY g.month
        """,
        (first, last_month),
    ).fetchall()
    return [CashFlowMonth(*r) for r in rows]


@dataclass
class NetWorth:
    as_of: date
    assets_cents: int            # money held in checking, savings, cash
    liabilities_cents: int       # money owed on cards
    accounts_not_started: int    # accounts whose opening balance is after this day (left out)

    @property
    def net_cents(self) -> int:
        return self.assets_cents - self.liabilities_cents


def net_worth(conn: psycopg.Connection, as_of: date) -> NetWorth:
    """Assets - liabilities at the end of a day, from computed balances (posted dates)."""
    row = conn.execute(
        """
        SELECT COALESCE(sum(b) FILTER (WHERE account_class = 'asset'), 0)::bigint,
               COALESCE(sum(b) FILTER (WHERE account_class = 'liability'), 0)::bigint,
               count(*) FILTER (WHERE b IS NULL)
          FROM (SELECT account_class, account_balance(id, %s) AS b FROM accounts) a
        """,
        (as_of,),
    ).fetchone()
    return NetWorth(as_of, *row)


def net_worth_series(conn: psycopg.Connection, last_month: date, today: date, months: int = 12) -> list[NetWorth]:
    """Net worth at the end of each month (today for the current month), oldest first. Future months are left out."""
    points = []
    for n in range(months - 1, -1, -1):
        month = add_months(last_month, -n)
        day = as_of_day(month, today)
        if day is not None:
            points.append(net_worth(conn, day))
    return points


@dataclass
class BooksCheck:
    """How a month's change in net worth breaks down (SPEC section 2, principle 8).

    All by POSTING date (balance_date), the same basis as balances, so the
    parts add up exactly. That's why income and spending here can differ a
    little from the cash-flow view, which follows purchase dates like budgets.
    """
    month: date
    net_worth_change_cents: int
    income_cents: int
    spending_cents: int
    adjustments_cents: int          # balance adjustments (Accounts page)
    transfers_cents: int            # transfer legs that don't cancel within the month: unmatched, or in transit
    unsorted_cents: int             # rows of unknown kind, still in Review
    openings_cents: int             # accounts whose opening balance falls in this month

    @property
    def explained_cents(self) -> int:
        """Income - spending + adjustments: what the change should be once nothing is pending."""
        return self.income_cents - self.spending_cents + self.adjustments_cents

    @property
    def pending_cents(self) -> int:
        """The part still waiting on a person: Review rows and transfers without their other side."""
        return self.transfers_cents + self.unsorted_cents

    @property
    def unexplained_cents(self) -> int:
        """Should always be 0. Anything else means the numbers don't add up (a bug worth reporting)."""
        return (self.net_worth_change_cents - self.explained_cents - self.pending_cents
                - self.openings_cents)


def books_check(conn: psycopg.Connection, month: date, through: date | None = None) -> BooksCheck:
    """Net worth change over a month (up to `through`, e.g. today, if that's earlier), and its parts."""
    end = min(month_end(month), through) if through else month_end(month)
    before = date.fromordinal(month.toordinal() - 1)     # last day of the previous month
    flows = dict(conn.execute(
        """
        SELECT COALESCE(t.kind, 'unsorted'), -sum(t.amount_cents)::bigint
          FROM transactions t JOIN accounts a ON a.id = t.account_id
         WHERE t.balance_date BETWEEN %s AND %s
           AND (a.opening_balance_date IS NULL OR t.balance_date > a.opening_balance_date)
         GROUP BY 1
        """,
        (month, end),
    ).fetchall())
    openings = conn.execute(
        """SELECT COALESCE(sum(CASE account_class WHEN 'asset' THEN opening_balance_cents
                                                 ELSE -opening_balance_cents END), 0)::bigint
             FROM accounts WHERE opening_balance_date BETWEEN %s AND %s""",
        (month, end),
    ).fetchone()[0]
    return BooksCheck(
        month=month,
        net_worth_change_cents=net_worth(conn, end).net_cents - net_worth(conn, before).net_cents,
        income_cents=flows.get("income", 0),
        spending_cents=-flows.get("expense", 0),
        adjustments_cents=flows.get("adjustment", 0),
        transfers_cents=flows.get("transfer", 0),
        unsorted_cents=flows.get("unsorted", 0),
        openings_cents=openings,
    )
