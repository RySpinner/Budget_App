"""Dashboard: this month's spending vs budget pace, the monthly trend, and a
drill-down from a top-level category to its subcategories.

Budget pace on a day = monthly budget x (day of month / days in month).
"""

from datetime import date

import pandas as pd
import streamlit as st

from app import charts
from app.ui import md
from budget.db import connect
from budget.money import format_cents
from budget.reports import (
    add_months, as_of_day, category_pace, category_trend, month_end, monthly_trend, subcategory_totals,
    uncategorized_cents,
)
from budget.transactions import months_with_data

STATUS_LABELS = {
    "On pace": "✅ On pace",
    "Over pace": "⚠️ Over pace",
    "Over budget": "⛔ Over budget",
    "No budget": "— No budget",
}

mode = "dark" if getattr(st.context.theme, "type", None) == "dark" else "light"
today = date.today()
this_month = today.replace(day=1)

st.title("Dashboard")

with connect() as conn:
    months = sorted(set(months_with_data(conn)) | {this_month}, reverse=True)

month = st.selectbox("Month", months, index=months.index(this_month),
                     format_func=lambda m: m.strftime("%B %Y"))
as_of = as_of_day(month, today)

with connect() as conn:
    pace_rows = category_pace(conn, month, as_of)
    uncategorized = uncategorized_cents(conn, month)
    trend = monthly_trend(conn, month)

names = {p.top_category_id: p.name for p in pace_rows}
category_order = [p.name for p in pace_rows]

# ---------------------------------------------------------------------------
# Headline numbers
# ---------------------------------------------------------------------------
total_spent = sum(p.spent_cents for p in pace_rows) + uncategorized
budgeted = [p for p in pace_rows if p.budget_cents is not None]
k1, k2, k3 = st.columns(3)
k1.metric("Spent this month" if month == this_month else f"Spent in {month:%B}", format_cents(total_spent))
if budgeted and as_of is not None:
    spent_b = sum(p.spent_cents for p in budgeted)
    pace_b = sum(p.pace_cents or 0 for p in budgeted)
    k2.metric("Budgeted categories vs pace", format_cents(spent_b),
              delta=f"{format_cents(spent_b - pace_b)} vs pace", delta_color="inverse")
else:
    k2.metric("Budgeted categories vs pace", "—")
k3.metric("Uncategorized", format_cents(uncategorized))
if uncategorized:
    k3.caption("Categorize these on the **Review** page.")

# ---------------------------------------------------------------------------
# Spending vs budget pace
# ---------------------------------------------------------------------------
st.header("Spending vs budget pace")
if as_of is None:
    st.write("This month hasn't started yet.")
else:
    days = month_end(month).day
    st.caption(md(f"Pace is measured on {as_of:%B} {as_of.day} (day {as_of.day} of {days}). "
                  "Bar = spent; wide track = full monthly budget; tick = where spending would be "
                  "if it went evenly through the month (for a finished month it sits at the full budget)."))
    if not budgeted:
        st.info(md("No budgets set yet. Add them on the **Budgets** page to see pace."))

    pace_df = pd.DataFrame([
        {"Category": p.name, "spent": p.spent_cents, "budget": p.budget_cents, "pace": p.pace_cents,
         "Spent": format_cents(p.spent_cents),
         "Pace to date": format_cents(p.pace_cents) if p.pace_cents is not None else "—",
         "Budget": format_cents(p.budget_cents) if p.budget_cents is not None else "—",
         "Status": STATUS_LABELS[p.status]}
        for p in pace_rows
    ])
    st.altair_chart(charts.pace_chart(pace_df, mode), width="stretch")
    st.dataframe(pace_df[["Category", "Spent", "Pace to date", "Budget", "Status"]],
                 hide_index=True, width="stretch")

# ---------------------------------------------------------------------------
# Month-over-month trend
# ---------------------------------------------------------------------------
st.header("Last 12 months")
if not trend:
    st.write("No spending recorded in this period yet.")
else:
    trend_df = pd.DataFrame([
        {"Month": t.month.strftime("%b %Y"), "month_start": t.month,
         "Category": names.get(t.top_category_id, charts.UNCATEGORIZED) if t.top_category_id else charts.UNCATEGORIZED,
         "cents": t.amount_cents, "Amount": format_cents(t.amount_cents)}
        for t in trend
    ])
    st.altair_chart(charts.trend_chart(trend_df, category_order, mode), width="stretch")
    with st.expander("Table"):
        table = trend_df.pivot_table(index="Category", columns="month_start", values="cents",
                                     aggfunc="sum", fill_value=0)
        table = table.reindex([c for c in category_order + [charts.UNCATEGORIZED] if c in table.index])
        table.columns = [m.strftime("%b %Y") for m in table.columns]
        st.dataframe(table.map(format_cents), width="stretch")

# ---------------------------------------------------------------------------
# Drill-down
# ---------------------------------------------------------------------------
st.header("Drill down")
id_by_name = {name: cat_id for cat_id, name in names.items()}
chosen = st.selectbox("Category", list(id_by_name))
top_id = id_by_name.get(chosen)
if top_id is not None:
    with connect() as conn:
        subs = subcategory_totals(conn, month, top_id)
        cat_trend = category_trend(conn, top_id, month)
    left, right = st.columns(2)
    with left:
        st.subheader(md(f"{names[top_id]} in {month:%B %Y}"))
        if subs:
            sub_df = pd.DataFrame([{"Subcategory": s.name, "cents": s.amount_cents,
                                    "Amount": format_cents(s.amount_cents)} for s in subs])
            st.altair_chart(charts.ranked_bars(sub_df, mode), width="stretch")
            st.dataframe(sub_df[["Subcategory", "Amount"]], hide_index=True, width="stretch")
        else:
            st.write("No spending in this category this month.")
    with right:
        st.subheader("Monthly spend vs budget")
        rows = []
        for m, spent, budget in cat_trend:
            rows.append({"Month": m.strftime("%b %Y"), "month_start": m, "Series": "Spent",
                         "cents": spent, "Amount": format_cents(spent)})
            if budget is not None:
                rows.append({"Month": m.strftime("%b %Y"), "month_start": m, "Series": "Budget",
                             "cents": budget, "Amount": format_cents(budget)})
        st.altair_chart(charts.category_trend_chart(pd.DataFrame(rows), mode), width="stretch")
        with st.expander("Table"):
            st.dataframe(pd.DataFrame([
                {"Month": m.strftime("%b %Y"), "Spent": format_cents(s),
                 "Budget": format_cents(b) if b is not None else "—"} for m, s, b in cat_trend
            ]), hide_index=True, width="stretch")
