"""Budgets page: set the monthly budget for each top-level category.

A new amount applies from the chosen month onward. Earlier months keep the
budget they had, so history is never rewritten.
"""

from datetime import date

import pandas as pd
import streamlit as st

from app.ui import md
from budget.budgets import budget_history, delete_budget, set_budget
from budget.db import connect
from budget.money import format_cents, parse_cents
from budget.reports import add_months

state = st.session_state
state.setdefault("budgets_round", 0)
state.setdefault("budgets_flash", None)

st.title("Budgets")
if state.budgets_flash:
    st.success(md(state.budgets_flash))
    state.budgets_flash = None

this_month = date.today().replace(day=1)
month_options = [add_months(this_month, n) for n in range(-12, 13)]
from_month = st.selectbox("Apply new amounts from", month_options, index=12,
                          format_func=lambda m: m.strftime("%B %Y"))

with connect() as conn:
    rows = conn.execute(
        """
        SELECT id, name, budget_for_month(id, %s)
          FROM categories WHERE parent_id IS NULL AND active
         ORDER BY sort_order, name
        """,
        (from_month,),
    ).fetchall()
    history = budget_history(conn)

st.caption("Type a monthly amount (e.g. 450 or 450.00) for any category you want to change; "
           "leave the rest blank. 0 means a budget of zero.")
table = pd.DataFrame([
    {"id": r[0], "Category": r[1],
     f"Budget in {from_month:%b %Y}": format_cents(r[2]) if r[2] is not None else "not set",
     "New amount": ""}
    for r in rows
])
edited = st.data_editor(
    table, key=f"budget_editor_{state.budgets_round}_{from_month}", hide_index=True, width="stretch",
    disabled=[c for c in table.columns if c != "New amount"],
    column_config={"id": None, "New amount": st.column_config.TextColumn(help="Monthly amount, e.g. 450.00")},
)

changes = [(int(r["id"]), r["Category"], str(r["New amount"] or "").strip())
           for _, r in edited.iterrows() if str(r["New amount"] or "").strip()]
if st.button(f"Save {len(changes)} budget{'s' if len(changes) != 1 else ''}", type="primary", disabled=not changes):
    errors, parsed = [], []
    for cat_id, name, text in changes:
        try:
            parsed.append((cat_id, name, parse_cents(text)))
        except ValueError as e:
            errors.append(f"{name}: {e}")
    if errors:
        for e in errors:
            st.error(md(e))
    else:
        try:
            with connect() as conn:
                for cat_id, _, cents in parsed:
                    set_budget(conn, cat_id, from_month, cents)
            state.budgets_flash = (f"Saved from {from_month:%B %Y}: "
                                   + ", ".join(f"{name} {format_cents(c)}" for _, name, c in parsed))
            state.budgets_round += 1
            st.rerun()
        except ValueError as e:
            st.error(md(e))

st.header("History")
if not history:
    st.write("No budgets set yet.")
else:
    hist = pd.DataFrame([
        {"id": b.id, "Category": b.category, "From": b.effective_from.strftime("%B %Y"),
         "Monthly amount": format_cents(b.monthly_amount_cents), "Delete": False}
        for b in history
    ])
    hist_edit = st.data_editor(
        hist, key=f"budget_history_{state.budgets_round}", hide_index=True, width="stretch",
        disabled=["Category", "From", "Monthly amount"], column_config={"id": None},
    )
    to_delete = [int(r["id"]) for _, r in hist_edit.iterrows() if r["Delete"]]
    st.caption("Deleting a row makes the previous row (if any) apply again from that month.")
    if st.button(f"Delete {len(to_delete)} row{'s' if len(to_delete) != 1 else ''}", disabled=not to_delete):
        with connect() as conn:
            for budget_id in to_delete:
                delete_budget(conn, budget_id)
        state.budgets_flash = f"Deleted {len(to_delete)} budget row{'s' if len(to_delete) != 1 else ''}."
        state.budgets_round += 1
        st.rerun()
