"""Dashboard: this month's spending vs budget pace, the monthly trend, a
drill-down from a top-level category to its subcategories, then money in and
out (cash flow), net worth, and the books check.

Budget pace on a day = monthly budget x (day of month / days in month).
Cash flow = income - spending by month (transfers never count). Net worth =
what the accounts hold - what's owed on the cards, from computed balances.
Accounts that disagree with the bank are flagged at the top.
"""

from datetime import date

import pandas as pd
import streamlit as st

from app import charts
from app.ui import md
from budget.balances import account_summaries
from budget.db import connect
from budget.money import format_cents
from budget.reports import (
    add_months, as_of_day, books_check, cash_flow, category_pace, category_trend, month_end, monthly_trend,
    net_worth, net_worth_series, subcategory_totals, uncategorized_cents,
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
    accounts_now = account_summaries(conn, today)

names = {p.top_category_id: p.name for p in pace_rows}
category_order = [p.name for p in pace_rows]

# ---------------------------------------------------------------------------
# Headline numbers
# ---------------------------------------------------------------------------
total_spent = sum(p.spent_cents for p in pace_rows) + uncategorized
budgeted = [p for p in pace_rows if p.budget_cents is not None]
k1, k2, k3, k4 = st.columns(4)
k1.metric("Spent this month" if month == this_month else f"Spent in {month:%B}", format_cents(total_spent))
if budgeted:
    spent_b = sum(p.spent_cents for p in budgeted)
    k2.metric("Monthly budget", format_cents(sum(p.budget_cents for p in budgeted)),
              help="Total of the categories that have a budget.")
    if spent_b != total_spent:
        k2.caption(md(f"{format_cents(spent_b)} spent in budgeted categories."))
else:
    k2.metric("Monthly budget", "—")
    k2.caption("Set budgets on the **Budgets** page.")
if budgeted and as_of is not None:
    pace_b = sum(p.pace_cents or 0 for p in budgeted)
    diff = spent_b - pace_b
    k3.metric("Pace to date", format_cents(pace_b),
              delta=f"{format_cents(abs(diff))} {'over' if diff > 0 else 'under'} pace" if diff else "on pace",
              delta_color="inverse" if diff > 0 else ("normal" if diff < 0 else "off"),
              delta_arrow="up" if diff > 0 else ("down" if diff < 0 else "off"),
              help="Where spending in budgeted categories would be if it went evenly through the month.")
else:
    k3.metric("Pace to date", "—")
k4.metric("Uncategorized", format_cents(uncategorized))
if uncategorized:
    k4.caption("Categorize these on the **Review** page.")

# Accounts that disagree with the bank (Accounts page) -----------------------
off = [a for a in accounts_now if a.reconciled_difference_cents or a.difference_cents]
if off:
    st.warning(md("Balances that don't match the bank: "
                  + "; ".join(f"{a.display_name} ({a.status.removeprefix('⚠ ')})" for a in off)
                  + ". Check them on the **Accounts** page."))

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


# ---------------------------------------------------------------------------
# Money in and out (cash flow)
# ---------------------------------------------------------------------------
st.header("Money in and out")
with connect() as conn:
    flows = cash_flow(conn, month)
current = flows[-1]
c1, c2, c3 = st.columns(3)
c1.metric("Income", format_cents(current.income_cents))
c2.metric("Spending", format_cents(current.spending_cents))
c3.metric("Saved" if current.saved_cents >= 0 else "Overspent", format_cents(abs(current.saved_cents)),
          help="Income minus spending. Transfers between your own accounts never count.")
st.caption("By purchase date, like the budgets. Transfers between your own accounts and balance adjustments "
           "aren't income or spending.")
if current.unsorted_count:
    rows_word = "row" if current.unsorted_count == 1 else "rows"
    direction = "in" if current.unsorted_cents > 0 else "out"
    st.caption(md(f"Not counted yet: {current.unsorted_count} {rows_word} whose kind isn't known "
                  f"({format_cents(abs(current.unsorted_cents))} {direction}). Sort them on the **Review** page."))
if any(f.income_cents or f.spending_cents for f in flows):
    flow_rows = []
    for f in flows:
        label = f.month.strftime("%b %Y")
        flow_rows.append({"Month": label, "month_start": f.month, "Series": "Income",
                          "cents": f.income_cents, "Amount": format_cents(f.income_cents)})
        flow_rows.append({"Month": label, "month_start": f.month, "Series": "Spending",
                          "cents": f.spending_cents, "Amount": format_cents(f.spending_cents)})
    st.altair_chart(charts.cash_flow_chart(pd.DataFrame(flow_rows), mode), width="stretch")
    with st.expander("Table"):
        st.dataframe(pd.DataFrame([
            {"Month": f.month.strftime("%b %Y"), "Income": format_cents(f.income_cents),
             "Spending": format_cents(f.spending_cents),
             "Saved (overspent)": format_cents(f.saved_cents),
             "Not sorted yet": f"{f.unsorted_count} rows" if f.unsorted_count else "—"}
            for f in reversed(flows)
        ]), hide_index=True, width="stretch")

# ---------------------------------------------------------------------------
# Net worth
# ---------------------------------------------------------------------------
st.header("Net worth")
nw_day = as_of_day(month, today) or today
with connect() as conn:
    nw = net_worth(conn, nw_day)
    nw_before = net_worth(conn, date.fromordinal(month.toordinal() - 1))
    series = net_worth_series(conn, month, today)
    month_accounts = account_summaries(conn, nw_day)
    check = books_check(conn, month, through=nw_day)

change = nw.net_cents - nw_before.net_cents
n1, n2, n3 = st.columns(3)
n1.metric(f"Net worth on {nw_day:%b} {nw_day.day}", format_cents(nw.net_cents),
          delta=f"{format_cents(abs(change))} {'up' if change >= 0 else 'down'} this month" if change else None,
          delta_color="normal", help="What the accounts hold minus what's owed on the cards.")
n2.metric("Held (checking, savings, cash)", format_cents(nw.assets_cents))
n3.metric("Owed (cards)", format_cents(nw.liabilities_cents))
missing_opening = [a.display_name for a in month_accounts if a.opening_balance_date is None]
if missing_opening:
    st.caption(md("No opening balance yet for " + ", ".join(missing_opening)
                  + ", so their balances assume they started at $0. Set one on the **Accounts** page."))
if nw.accounts_not_started:
    st.caption(f"{nw.accounts_not_started} account(s) start after this day (their opening balance is later) "
               "and aren't included.")

if len(series) > 1:
    nw_df = pd.DataFrame([{"Month": p.as_of.strftime("%b %Y"), "month_start": p.as_of.replace(day=1),
                           "cents": p.net_cents, "Amount": format_cents(p.net_cents)} for p in series])
    st.altair_chart(charts.net_worth_chart(nw_df, mode), width="stretch")
    with st.expander("Table"):
        st.dataframe(pd.DataFrame([
            {"On": f"{p.as_of:%b} {p.as_of.day}, {p.as_of:%Y}", "Held": format_cents(p.assets_cents),
             "Owed": format_cents(p.liabilities_cents), "Net worth": format_cents(p.net_cents)}
            for p in reversed(series)
        ]), hide_index=True, width="stretch")

st.subheader("Accounts")
st.dataframe(pd.DataFrame([
    {"Account": a.display_name, "Balance": "—" if a.balance_cents is None else format_cents(a.balance_cents),
     "Held / owed": "owed" if a.account_class == "liability" else "held", "Status": a.status}
    for a in month_accounts
]), hide_index=True, width="stretch")

check_period = f"{month:%B %Y}" if nw_day == month_end(month) else f"{month:%B} 1–{nw_day.day}, {month:%Y}"
with st.expander(f"Does it add up? (books check for {check_period})"):
    st.caption("Every change in net worth comes from income, spending or a balance adjustment, once everything "
               "is sorted. Transfers between your own accounts cancel out. Counted by posting date, like the "
               "balances, so income and spending can differ slightly from the figures above.")
    lines = [
        ("Income", check.income_cents),
        ("Spending", -check.spending_cents),
        ("Balance adjustments", check.adjustments_cents),
        ("= Income - spending + adjustments", check.explained_cents),
        ("Still in Review (kind not known)", check.unsorted_cents),
        ("Transfers whose other side isn't in this month (unmatched, or still in transit)", check.transfers_cents),
        ("Accounts starting this month (opening balances)", check.openings_cents),
        ("Change in net worth", check.net_worth_change_cents),
    ]
    st.dataframe(pd.DataFrame([{"": name, "Amount": format_cents(cents)} for name, cents in lines]),
                 hide_index=True, width="stretch")
    if check.unexplained_cents:
        st.error(md(f"The parts don't add up: {format_cents(check.unexplained_cents)} is unexplained. "
                    "This points to a bug; please report it."))
    elif check.pending_cents:
        st.info(md(f"Adds up, with {format_cents(abs(check.pending_cents))} still waiting in Review or on a transfer's "
                   "other side. Once those are sorted, the change in net worth is exactly income - spending + "
                   "adjustments."))
    else:
        st.success("✓ Adds up: the change in net worth is exactly income - spending + adjustments"
                   + (" + opening balances." if check.openings_cents else "."))
