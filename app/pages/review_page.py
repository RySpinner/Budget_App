"""Review page: categorize uncategorized transactions, optionally saving rules.

Pick a category for any rows you want to handle now (leave the rest blank),
tick "Save as rule" to categorize similar rows automatically from now on,
then press Save.
"""

import pandas as pd
import streamlit as st

from budget.categories import subcategory_choices
from app.ui import md
from budget.db import connect
from budget.money import format_cents
from budget.rules import categorize_manually, suggest_pattern



def plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


state = st.session_state
state.setdefault("review_round", 0)   # bump to reset the editor after saving
state.setdefault("review_flash", None)

st.title("Review")

if state.review_flash:
    st.success(md(state.review_flash))
    state.review_flash = None

with connect() as conn:
    choices = subcategory_choices(conn)
    rows = conn.execute(
        """
        SELECT t.id, t.effective_date, t.amount_cents, c.display_name, t.merchant, t.raw_description
          FROM transactions t
          LEFT JOIN cards c ON c.id = t.card_id
         WHERE t.category_id IS NULL
           AND NOT EXISTS (SELECT 1 FROM splits s WHERE s.transaction_id = t.id)  -- split lines carry the categories
         ORDER BY t.effective_date DESC, t.id
        """
    ).fetchall()

if not rows:
    st.write("Nothing to review: every transaction has a category.")
    st.stop()

st.caption(
    f"{len(rows)} uncategorized. Choose a category for the rows you want to handle; "
    "blank rows stay in the queue. A saved rule uses the pattern shown (matched anywhere in "
    "the bank description, ignoring case) and also applies to other uncategorized rows."
)

label_to_id = {label: cat_id for cat_id, label in choices.items()}
table = pd.DataFrame(
    [
        {
            "id": r[0],
            "Date": r[1],
            "Amount": format_cents(r[2]),
            "Card": r[3] or "Cash / manual",
            "Merchant": r[4],
            "Category": None,
            "Save as rule": False,
            "Rule pattern": suggest_pattern(r[4], r[5]),
            "Bank description": r[5],
        }
        for r in rows
    ]
)

edited = st.data_editor(
    table,
    key=f"review_editor_{state.review_round}",
    hide_index=True,
    width="stretch",
    disabled=["Date", "Amount", "Card", "Merchant", "Bank description"],
    column_config={
        "id": None,  # hidden, but kept so we know which row is which
        "Category": st.column_config.SelectboxColumn(options=list(label_to_id), width="medium"),
        "Save as rule": st.column_config.CheckboxColumn(),
        "Rule pattern": st.column_config.TextColumn(width="medium"),
    },
)

chosen = edited[edited["Category"].notna()]
if st.button(f"Save {plural(len(chosen), 'categorized row')}", type="primary", disabled=chosen.empty):
    problems = [
        f"{row['Merchant']}: 'Save as rule' is ticked but the pattern is empty"
        for _, row in chosen.iterrows()
        if row["Save as rule"] and not str(row["Rule pattern"] or "").strip()
    ]
    if problems:
        for p in problems:
            st.error(md(p))
        st.stop()

    rules_made, also_categorized = 0, 0
    with connect() as conn:
        for _, row in chosen.iterrows():
            pattern = str(row["Rule pattern"]).strip() if row["Save as rule"] else None
            rule_id, others = categorize_manually(conn, int(row["id"]), label_to_id[row["Category"]], pattern)
            rules_made += rule_id is not None
            also_categorized += others

    message = f"Saved {plural(len(chosen), 'row')}."
    if rules_made:
        message += (f" Created {plural(rules_made, 'rule')}, which categorized "
                    f"{plural(also_categorized, 'other row')}.")
    state.review_flash = message
    state.review_round += 1
    st.rerun()
