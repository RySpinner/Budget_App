"""Transactions page: filter, edit, split, and add manual (cash) entries.

Choosing one top-level category gives the old per-sheet layout: date,
amount, running monthly total, merchant, card, budget-to-date, note,
purchaser. Running totals and budget-to-date are computed for display only.
"""

from datetime import date

import pandas as pd
import streamlit as st

from budget.categories import subcategory_choices
from budget.db import connect
from budget.money import format_cents, format_plain, parse_cents
from app.ui import md
from budget.transactions import (
    CASH, UNCATEGORIZED, Filters, SplitLine, add_manual, delete_manual, get_splits, load_lines,
    months_with_data, set_splits, update_line,
)

state = st.session_state
state.setdefault("txn_round", 0)     # bump to reset editors after saving
state.setdefault("txn_flash", None)

st.title("Transactions")

if state.txn_flash:
    st.success(md(state.txn_flash))
    state.txn_flash = None

with connect() as conn:
    categories = subcategory_choices(conn)                       # id -> "Top › Sub"
    all_categories = subcategory_choices(conn, include_inactive=True)
    tops = dict(conn.execute(
        "SELECT id, name FROM categories WHERE parent_id IS NULL AND active ORDER BY sort_order, name"
    ).fetchall())
    cards = dict(conn.execute("SELECT id, display_name FROM cards ORDER BY display_name").fetchall())
    people = dict(conn.execute("SELECT id, name FROM people ORDER BY name").fetchall())
    months = months_with_data(conn)

if not people:
    st.warning("Add people first (seed/local.toml, then `python -m budget.seed`).")
    st.stop()

# Dropdowns offer active categories; old rows may still show a retired one.
category_label_to_id = {label: cid for cid, label in all_categories.items()}
active_category_labels = list(categories.values())
person_name_to_id = {name: pid for pid, name in people.items()}


def money_or_blank(cents: int | None) -> str:
    return "" if cents is None else format_cents(cents)


def blank_to_none(value):
    """data_editor may hand back None, NaN or "" for an empty cell; treat them alike."""
    if value is None or (not isinstance(value, str) and pd.isna(value)) or value == "":
        return None
    return value


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
today = date.today()
this_month = today.replace(day=1)
month_options = sorted(set(months) | {this_month}, reverse=True)

c1, c2, c3, c4, c5 = st.columns(5)
month = c1.selectbox("Month", [None] + month_options, index=1,
                     format_func=lambda m: "All months" if m is None else m.strftime("%B %Y"))
top_id = c2.selectbox("Category", [None, *tops, UNCATEGORIZED],
                      format_func=lambda t: {None: "All categories", UNCATEGORIZED: "Uncategorized"}.get(t, tops.get(t)))
sub_options = [None] + [cid for cid, label in categories.items()
                        if top_id not in (None, UNCATEGORIZED) and label.startswith(tops[top_id] + " › ")]
sub_id = c3.selectbox("Subcategory", sub_options, disabled=len(sub_options) == 1,
                      format_func=lambda s: "All" if s is None else categories[s].split(" › ", 1)[1])
card_id = c4.selectbox("Card", [None, *cards, CASH],
                       format_func=lambda c: {None: "All cards", CASH: "Cash / manual"}.get(c, cards.get(c)))
purchaser_id = c5.selectbox("Purchaser", [None, *people],
                            format_func=lambda p: "Everyone" if p is None else people[p])

filters = Filters(month=month, top_category_id=top_id, category_id=sub_id, card_id=card_id,
                  purchaser_id=purchaser_id)
sheet_view = top_id not in (None, UNCATEGORIZED)

with connect() as conn:
    lines = load_lines(conn, filters)
    budget = None
    if sheet_view and month is not None:
        budget = conn.execute("SELECT budget_for_month(%s, %s)", (top_id, month)).fetchone()[0]

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
total = sum(line.amount_cents for line in lines)
m1, m2, m3 = st.columns(3)
m1.metric("Total shown", format_cents(total))
m2.metric("Lines", len(lines))
if budget is not None:
    m3.metric("Monthly budget", format_cents(budget))
elif sheet_view and month is not None:
    m3.metric("Monthly budget", "not set")

if card_id is not None or purchaser_id is not None or sub_id is not None:
    st.caption("Running totals add up the rows shown, so with card / purchaser / subcategory "
               "filters they cover only those rows.")

# ---------------------------------------------------------------------------
# Editable table
# ---------------------------------------------------------------------------
if not lines:
    st.write("No transactions match these filters.")
else:
    table = pd.DataFrame(
        [
            {
                "transaction_id": line.transaction_id,
                "split_id": line.split_id,
                "Date": line.effective_date,
                "Amount": format_cents(line.amount_cents),
                "Running total": format_cents(line.running_total_cents),
                "Merchant": line.merchant,
                "Card": line.card or "Cash / manual",
                "Budget to date": money_or_blank(line.budget_to_date_cents),
                "Category": all_categories.get(line.category_id),
                "Note": line.note or "",
                "Purchaser": people[line.purchaser_id],
                "Split": "" if line.split_id is None else f"part of {line.split_count}",
                "Bank description": line.raw_description,
            }
            for line in lines
        ]
    )
    if not sheet_view:
        table = table.drop(columns=["Budget to date"])

    edited = st.data_editor(
        table,
        key=f"txn_editor_{state.txn_round}_{month}_{top_id}_{sub_id}_{card_id}_{purchaser_id}",
        hide_index=True,
        width="stretch",
        disabled=[c for c in table.columns if c not in ("Category", "Note", "Purchaser")],
        column_config={
            "transaction_id": None,
            "split_id": None,
            "Category": st.column_config.SelectboxColumn(options=active_category_labels, width="medium"),
            "Purchaser": st.column_config.SelectboxColumn(options=list(person_name_to_id), required=True),
            "Note": st.column_config.TextColumn(width="medium"),
        },
    )

    editable = ("Category", "Note", "Purchaser")
    changed = [
        (i, row) for i, row in edited.iterrows()
        if any(blank_to_none(row[c]) != blank_to_none(table.at[i, c]) for c in editable)
    ]
    if st.button(f"Save {len(changed)} changed row{'s' if len(changed) != 1 else ''}",
                 type="primary", disabled=not changed):
        errors = []
        with connect() as conn:
            for _, row in changed:
                split_id = None if pd.isna(row["split_id"]) else int(row["split_id"])
                try:
                    update_line(
                        conn, int(row["transaction_id"]), split_id,
                        category_id=category_label_to_id[row["Category"]] if blank_to_none(row["Category"]) else None,
                        note=blank_to_none(row["Note"]),
                        purchaser_id=person_name_to_id[row["Purchaser"]],
                    )
                except (ValueError, KeyError) as e:
                    errors.append(f"{row['Date']} {row['Merchant']}: {e}")
        if errors:
            for e in errors:
                st.error(md(e))
        else:
            state.txn_flash = f"Saved {len(changed)} row{'s' if len(changed) != 1 else ''}."
            state.txn_round += 1
            st.rerun()

# ---------------------------------------------------------------------------
# One transaction: splits and deleting manual entries
# ---------------------------------------------------------------------------
st.header("Split or delete a transaction")

by_txn = {}
for line in lines:
    by_txn.setdefault(line.transaction_id, line)
txn_id = st.selectbox(
    "Transaction (from the rows above)",
    list(by_txn),
    index=None,
    placeholder="Choose a transaction",
    format_func=lambda t: f"{by_txn[t].effective_date}  ·  {by_txn[t].merchant}  ·  "
                          f"{format_cents(sum(l.amount_cents for l in lines if l.transaction_id == t))}"
                          + ("  ·  (shown in part: other split lines are filtered out)"
                             if by_txn[t].split_count and sum(1 for l in lines if l.transaction_id == t) < by_txn[t].split_count
                             else ""),
)

if txn_id is not None:
    with connect() as conn:
        txn = conn.execute(
            "SELECT amount_cents, category_id, source, raw_description FROM transactions WHERE id = %s", (txn_id,)
        ).fetchone()
        existing = get_splits(conn, txn_id)
    amount_cents, parent_category, source, raw = txn
    st.write(md(f"**{format_cents(amount_cents)}** · {raw}"))

    st.markdown("**Split across categories**")
    st.caption("Amounts like 12.34 (use a minus sign for refunds). Lines must add up exactly "
               "to the transaction. Add or remove lines with the table's + and delete controls.")
    start = existing or [SplitLine(amount_cents, parent_category), SplitLine(0, None)]
    split_table = pd.DataFrame([
        {"Amount": format_plain(l.amount_cents) if l.amount_cents else "",
         "Category": all_categories.get(l.category_id),
         "Note": l.note or ""}
        for l in start
    ])
    split_edit = st.data_editor(
        split_table,
        key=f"split_editor_{txn_id}_{state.txn_round}",
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        column_config={
            "Amount": st.column_config.TextColumn(required=True),
            "Category": st.column_config.SelectboxColumn(options=active_category_labels, required=True),
            "Note": st.column_config.TextColumn(),
        },
    )

    split_lines, problems = [], []
    for _, row in split_edit.iterrows():
        text = str(row["Amount"] or "").strip()
        if not text and not blank_to_none(row["Category"]):
            continue  # ignore blank rows
        if not text:
            problems.append(f"The {row['Category']} line needs an amount")
            continue
        try:
            cents = parse_cents(text)
        except ValueError as e:
            problems.append(str(e))
            continue
        if not blank_to_none(row["Category"]):
            problems.append(f"Line {format_cents(cents)} needs a category")
            continue
        split_lines.append(SplitLine(cents, category_label_to_id[row["Category"]], blank_to_none(row["Note"])))

    lines_total = sum(l.amount_cents for l in split_lines)
    remaining = amount_cents - lines_total
    st.write(md(f"Lines total {format_cents(lines_total)} of {format_cents(amount_cents)}"
                + ("" if remaining == 0 else f" — **{format_cents(remaining)} left to assign**")))
    for p in problems:
        st.error(md(p))

    b1, b2, _ = st.columns([1, 1, 3])
    if b1.button("Save split", type="primary", disabled=bool(problems) or remaining != 0 or len(split_lines) < 2):
        try:
            with connect() as conn:
                set_splits(conn, txn_id, split_lines)
            state.txn_flash = f"Split into {len(split_lines)} lines."
            state.txn_round += 1
            st.rerun()
        except ValueError as e:
            st.error(md(e))
    if existing and b2.button("Remove split"):
        with connect() as conn:
            set_splits(conn, txn_id, [])
        state.txn_flash = "Split removed; the transaction uses its own category again."
        state.txn_round += 1
        st.rerun()

    if source == "manual":
        st.markdown("**Delete this manual entry**")
        sure = st.checkbox("Yes, delete it", key=f"confirm_delete_{txn_id}")
        if st.button("Delete", disabled=not sure):
            with connect() as conn:
                delete_manual(conn, txn_id)
            state.txn_flash = "Manual entry deleted."
            state.txn_round += 1
            st.rerun()

# ---------------------------------------------------------------------------
# Manual entry
# ---------------------------------------------------------------------------
with st.expander("Add a cash / manual entry"):
    with st.form("manual_entry", clear_on_submit=True):
        f1, f2 = st.columns(2)
        entry_date = f1.date_input("Date", value=today)
        amount_text = f2.text_input("Amount", placeholder="12.34", help="Use a minus sign for a refund")
        description = st.text_input("Description", placeholder="e.g. Farmers market")
        f3, f4 = st.columns(2)
        category_label = f3.selectbox("Category", active_category_labels, index=None,
                                      placeholder="Leave blank to categorize later")
        purchaser = f4.selectbox("Purchaser", list(person_name_to_id))
        note = st.text_input("Note (optional)")
        if st.form_submit_button("Add entry"):
            try:
                with connect() as conn:
                    add_manual(
                        conn,
                        txn_date=entry_date,
                        amount_cents=parse_cents(amount_text),
                        description=description,
                        purchaser_id=person_name_to_id[purchaser],
                        category_id=category_label_to_id[category_label] if category_label else None,
                        note=note,
                    )
                state.txn_flash = f"Added {description.strip()} ({format_cents(parse_cents(amount_text))})."
                state.txn_round += 1
                st.rerun()
            except ValueError as e:
                st.error(md(e))
