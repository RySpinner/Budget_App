"""Review page: categorize uncategorized transactions, optionally saving rules.

Pick a category for any rows you want to handle now (leave the rest blank),
tick "Save as rule" to categorize similar rows automatically from now on,
then press Save.

Choosing "Transfer between our accounts" makes the row a transfer. A rule
saved from it marks similar rows *in the same account* as transfers, and
can name the account on the other side ("Other account"), which helps the
transfer matcher (phase 10).
"""

import pandas as pd
import streamlit as st

from budget.categories import subcategory_choices
from app.ui import md
from budget.db import connect
from budget.money import format_cents
from budget.rules import categorize_manually, mark_transfer_manually, suggest_pattern

TRANSFER_LABEL = "↔ Transfer between our accounts"



def plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


state = st.session_state
state.setdefault("review_round", 0)   # bump to reset the editor after saving
state.setdefault("review_flash", None)

st.title("Review")

if state.review_flash:
    st.success(md(state.review_flash))
    state.review_flash = None

NO_ACCOUNT = "—"

with connect() as conn:
    choices = subcategory_choices(conn)
    accounts = dict(conn.execute("SELECT id, display_name FROM accounts WHERE active ORDER BY display_name"))
    rows = conn.execute(
        """
        SELECT t.id, t.effective_date, t.amount_cents, a.display_name, t.merchant, t.raw_description,
               t.account_id
          FROM transactions t
          JOIN accounts a ON a.id = t.account_id
         WHERE t.category_id IS NULL
           AND t.kind IS DISTINCT FROM 'transfer'   -- transfers have no category; they get their own list later
           AND NOT EXISTS (SELECT 1 FROM splits s WHERE s.transaction_id = t.id)  -- split lines carry the categories
         ORDER BY t.effective_date DESC, t.id
        """
    ).fetchall()

if not rows:
    st.write("Nothing to review: every transaction has a category.")
    st.stop()

st.caption(
    f"{len(rows)} uncategorized. Choose a category (spending or income) for the rows you want to "
    f"handle, or **{TRANSFER_LABEL}** for money moving between your own accounts. Blank rows stay "
    "in the queue. A saved rule uses the pattern shown (matched anywhere in the bank description, "
    "ignoring case) and also applies to other uncategorized rows. For a transfer, the rule only "
    "applies to the same account; **Other account** (optional) says where the money went or came from."
)
account_label_to_id = {name: aid for aid, name in accounts.items()}

label_to_id = {label: cat_id for cat_id, label in choices.items()}
category_options = list(label_to_id) + [TRANSFER_LABEL]
table = pd.DataFrame(
    [
        {
            "id": r[0],
            "Date": r[1],
            "Amount": format_cents(r[2]),
            "Account": r[3],
            "Merchant": r[4],
            "Category": None,
            "Other account": NO_ACCOUNT,
            "Save as rule": False,
            "Rule pattern": suggest_pattern(r[4], r[5]),
            "Bank description": r[5],
            "account_id": r[6],
        }
        for r in rows
    ]
)

edited = st.data_editor(
    table,
    key=f"review_editor_{state.review_round}",
    hide_index=True,
    width="stretch",
    disabled=["Date", "Amount", "Account", "Merchant", "Bank description"],
    column_config={
        "id": None,  # hidden, but kept so we know which row is which
        "account_id": None,
        "Category": st.column_config.SelectboxColumn(options=category_options, width="medium"),
        "Save as rule": st.column_config.CheckboxColumn(),
        "Rule pattern": st.column_config.TextColumn(width="medium"),
        "Other account": st.column_config.SelectboxColumn(
            options=[NO_ACCOUNT] + list(account_label_to_id), width="medium",
            help="Transfers only: the other account of yours the money moved to or from (optional)"),
    },
)

chosen = edited[edited["Category"].notna()]
if st.button(f"Save {plural(len(chosen), 'categorized row')}", type="primary", disabled=chosen.empty):
    problems = [
        f"{row['Merchant']}: 'Save as rule' is ticked but the pattern is empty"
        for _, row in chosen.iterrows()
        if row["Save as rule"] and not str(row["Rule pattern"] or "").strip()
    ] + [
        f"{row['Merchant']}: 'Other account' is only for transfers"
        for _, row in chosen.iterrows()
        if row["Category"] != TRANSFER_LABEL and row["Other account"] not in (None, NO_ACCOUNT)
    ] + [
        f"{row['Merchant']}: the other account can't be the same account"
        for _, row in chosen.iterrows()
        if account_label_to_id.get(row["Other account"]) == row["account_id"]
    ]
    if problems:
        for p in problems:
            st.error(md(p))
        st.stop()

    rules_made, also_categorized = 0, 0
    with connect() as conn:
        for _, row in chosen.iterrows():
            pattern = str(row["Rule pattern"]).strip() if row["Save as rule"] else None
            if row["Category"] == TRANSFER_LABEL:
                rule_id, others = mark_transfer_manually(
                    conn, int(row["id"]), pattern,
                    counterpart_account_id=account_label_to_id.get(row["Other account"]),
                    rule_account_id=int(row["account_id"]),
                )
            else:
                rule_id, others = categorize_manually(conn, int(row["id"]), label_to_id[row["Category"]], pattern)
            rules_made += rule_id is not None
            also_categorized += others

    message = f"Saved {plural(len(chosen), 'row')}."
    if rules_made:
        message += (f" Created {plural(rules_made, 'rule')}, which sorted "
                    f"{plural(also_categorized, 'other row')}.")
    state.review_flash = message
    state.review_round += 1
    st.rerun()
