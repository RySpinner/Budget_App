"""Accounts page: balances, reconciliation with the bank, and account settings.

- The table lists every account with its computed balance today, the latest
  balance the bank stated, and the difference between them on that day.
  Assets show money held; credit cards show money owed.
- Choosing an account shows its reconciliation history and lets you:
  set the opening balance, close an unexplained difference with a balance
  adjustment, mark it reconciled, type in a balance from a statement, and
  change its details.
- New accounts are added at the bottom.

All numbers are computed on request (budget/balances.py); nothing is stored
except what a person or an import entered.
"""

from datetime import date

import pandas as pd
import streamlit as st

from app.ui import md
from budget.balances import (
    ACCOUNT_TYPES, account_summaries, add_adjustment, delete_snapshot, earliest_snapshot,
    reconciliation_history, rows_before_opening, save_account, save_snapshot, set_opening_balance,
    set_reconciled_through,
)
from budget.db import connect
from budget.money import format_cents, format_plain, parse_cents
from budget.parsers import PARSERS

TYPE_LABELS = {"checking": "Checking", "savings": "Savings", "cash": "Cash", "credit_card": "Credit card"}
SOURCE_LABELS = {"import": "from an import", "statement": "statement", "manual": "typed in"}
NO_PARSER = "(none: entered by hand)"

state = st.session_state
state.setdefault("accounts_round", 0)
state.setdefault("accounts_flash", None)


def long_date(d: date) -> str:
    return f"{d:%b} {d.day}, {d:%Y}"


def money_or_dash(cents: int | None) -> str:
    return "—" if cents is None else format_cents(cents)


def finish(message: str) -> None:
    state.accounts_flash = message
    state.accounts_round += 1
    st.rerun()


def held_or_owed(account_class: str) -> str:
    return "held" if account_class == "asset" else "owed"


st.title("Accounts")
if state.accounts_flash:
    st.success(md(state.accounts_flash))
    state.accounts_flash = None

today = date.today()
show_inactive = st.toggle("Show inactive accounts", value=False)
with connect() as conn:
    summaries = account_summaries(conn, today, include_inactive=show_inactive)
    people = dict(conn.execute("SELECT id, name FROM people ORDER BY name").fetchall())

st.caption("Balances are worked out from each account's opening balance plus its transactions (by posted date, "
           "as the bank counts them). Checking, savings and cash show money **held**; credit cards show money "
           "**owed**. The difference is the bank's balance minus ours on the bank's latest date; anything but "
           "zero means a missing or duplicated row, or a wrong opening balance.")

if summaries:
    st.dataframe(
        pd.DataFrame([
            {
                "Account": a.display_name,
                "Type": TYPE_LABELS.get(a.account_type, a.account_type),
                "Owner": a.owner,
                "Balance today": money_or_dash(a.balance_cents),
                "Bank balance": (f"{format_cents(a.bank_cents)} on {long_date(a.bank_date)}"
                                 if a.bank_date else "—"),
                "Difference": money_or_dash(a.difference_cents),
                "Reconciled through": long_date(a.reconciled_through) if a.reconciled_through else "—",
                "Status": a.status,
            }
            for a in summaries
        ]),
        hide_index=True, width="stretch",
    )

# ---------------------------------------------------------------------------
# One account
# ---------------------------------------------------------------------------
by_id = {a.id: a for a in summaries}
account_id = st.selectbox("Account details", list(by_id), index=None, format_func=lambda i: by_id[i].display_name,
                          placeholder="Choose an account to reconcile or edit")

if account_id is not None:
    a = by_id[account_id]
    word = held_or_owed(a.account_class)
    with connect() as conn:
        history = reconciliation_history(conn, account_id)
        first_bank = earliest_snapshot(conn, account_id)
        early_count, early_last = rows_before_opening(conn, account_id)

    m1, m2, m3 = st.columns(3)
    m1.metric(f"Balance today ({word})", money_or_dash(a.balance_cents))
    m2.metric("Bank's latest balance", money_or_dash(a.bank_cents),
              help=f"On {long_date(a.bank_date)}" if a.bank_date else None)
    m3.metric("Difference", money_or_dash(a.difference_cents))

    # Opening balance ---------------------------------------------------------
    if a.opening_balance_date is None:
        st.info("No opening balance yet, so the balance assumes this account held (or owed) $0 before its "
                "first transaction. Set one below: the balance at the end of a day, e.g. from a statement.")
        if first_bank and st.button(f"Use the bank's earliest balance ({format_cents(first_bank[1])} on "
                                    f"{long_date(first_bank[0])}) as the opening balance"):
            with connect() as conn:
                set_opening_balance(conn, account_id, first_bank[1], first_bank[0])
            finish(f"Opening balance set to {format_cents(first_bank[1])} at the end of {long_date(first_bank[0])}.")
    else:
        st.caption(md(f"Opening balance: {format_cents(a.opening_balance_cents)} {word} at the end of "
                      f"{long_date(a.opening_balance_date)}. Transactions on or before that day are already "
                      "included in it."))
        if early_count:
            st.info(md(
                f"{early_count} transaction{'s are' if early_count != 1 else ' is'} dated on or before the opening "
                f"balance date (the latest on {long_date(early_last)}), so {'they don' if early_count != 1 else 'it doesn'}"
                "'t change the balance. That's right if the opening balance already includes "
                f"{'them' if early_count != 1 else 'it'}. If not, move the opening balance to an earlier day "
                "(under Edit this account), or check the transaction's date."))

    # Reconciliation history ---------------------------------------------------
    st.subheader("Reconciliation")
    if not history:
        st.write("No bank balances yet. They come from an import with a balance column, or type one in below "
                 "(e.g. from a statement).")
    else:
        st.dataframe(
            pd.DataFrame([
                {
                    "Date": long_date(h.as_of_date),
                    "Bank says": format_cents(h.bank_cents),
                    "Our balance": money_or_dash(h.computed_cents),
                    "Difference": ("before the opening balance" if h.difference_cents is None
                                   else "✓ 0" if h.difference_cents == 0 else format_cents(h.difference_cents)),
                    "Source": SOURCE_LABELS.get(h.source, h.source),
                }
                for h in history
            ]),
            hide_index=True, width="stretch",
        )
        latest = history[0]
        if latest.difference_cents and a.opening_balance_date is None:
            st.info("Set an opening balance first (above). Without one, a difference here usually just means "
                    "the account didn't start at $0, so a balance adjustment isn't offered yet.")
        elif latest.difference_cents:
            st.warning(md(
                f"On {long_date(latest.as_of_date)} the bank says {format_cents(latest.bank_cents)} but the "
                f"transactions add up to {format_cents(latest.computed_cents)}: a difference of "
                f"{format_cents(latest.difference_cents)}. Usual causes: a file not imported (or imported "
                "twice), a hand entry on the wrong account, or the opening balance. If you can't find it, "
                "record a balance adjustment; it changes the balance but never counts as spending or income."
            ))
            adj_note = st.text_input("Note for the adjustment (optional)", key=f"adj_note_{state.accounts_round}")
            if st.button(f"Add a balance adjustment of {format_cents(latest.difference_cents)} on "
                         f"{long_date(latest.as_of_date)}"):
                try:
                    with connect() as conn:
                        add_adjustment(conn, account_id, latest.as_of_date, latest.difference_cents, adj_note)
                    finish("Balance adjustment added. It's listed on the Transactions page under "
                           "Kind → Balance adjustments, where it can be deleted.")
                except ValueError as e:
                    st.error(md(e))
        elif latest.difference_cents == 0 and a.reconciled_through != latest.as_of_date:
            if st.button(f"Mark reconciled through {long_date(latest.as_of_date)}",
                         help="You'll be warned before changes that would alter balances on or before this day."):
                with connect() as conn:
                    set_reconciled_through(conn, account_id, latest.as_of_date)
                finish(f"{a.display_name} is reconciled through {long_date(latest.as_of_date)}.")
        if a.reconciled_difference_cents:
            st.error(md(
                f"This account was reconciled through {long_date(a.reconciled_through)}, but its balance on that "
                f"day no longer matches the bank (off by {format_cents(a.reconciled_difference_cents)}). "
                "Something on or before that day changed: an undone import, or a manual entry added or deleted."
            ))

    # Typing in a bank balance -------------------------------------------------
    with st.expander("Enter a balance from the bank (e.g. a statement)"):
        with st.form(f"snapshot_{account_id}_{state.accounts_round}"):
            s1, s2, s3 = st.columns(3)
            snap_date = s1.date_input("Date (end of day)", value=today)
            snap_amount = s2.text_input(f"Balance ({word})", placeholder="1234.56",
                                        help="As the bank states it: money in the account, or owed on the card")
            snap_source = s3.selectbox("From", ["statement", "manual"],
                                       format_func={"statement": "A statement", "manual": "The website / app"}.get)
            if st.form_submit_button("Save balance"):
                try:
                    with connect() as conn:
                        save_snapshot(conn, account_id, snap_date, parse_cents(snap_amount), snap_source)
                    finish(f"Saved the bank's balance for {long_date(snap_date)}.")
                except ValueError as e:
                    st.error(md(e))
        typed = [h for h in history if h.source != "import"]
        if typed:
            gone = st.selectbox("Delete a balance you typed in", [h.snapshot_id for h in typed], index=None,
                                format_func=lambda i: next(f"{long_date(h.as_of_date)}: {format_cents(h.bank_cents)}"
                                                           for h in typed if h.snapshot_id == i))
            if st.button("Delete balance", disabled=gone is None):
                with connect() as conn:
                    delete_snapshot(conn, gone)
                finish("Balance deleted.")

    # Account details ----------------------------------------------------------
    with st.expander("Edit this account"):
        with st.form(f"edit_{account_id}_{state.accounts_round}"):
            e1, e2 = st.columns(2)
            name = e1.text_input("Name", value=a.display_name)
            institution = e2.text_input("Institution", value=a.institution)
            e3, e4, e5 = st.columns(3)
            types = list(ACCOUNT_TYPES)
            acct_type = e3.selectbox("Type", types, index=types.index(a.account_type) if a.account_type in types
                                     else 0, format_func=TYPE_LABELS.get)
            owner = e4.selectbox("Owner", list(people), index=list(people).index(a.owner_id),
                                 format_func=people.get)
            last4 = e5.text_input("Last 4 digits", value=a.last4 or "")
            parsers = [NO_PARSER] + sorted(PARSERS)
            parser = st.selectbox("CSV format (parser)", parsers,
                                  index=parsers.index(a.parser_key) if a.parser_key in parsers else 0)
            o1, o2 = st.columns(2)
            opening_text = o1.text_input(f"Opening balance ({word})",
                                         value=format_plain(a.opening_balance_cents)
                                         if a.opening_balance_cents is not None else "",
                                         help="Leave blank for none")
            opening_date = o2.date_input("At the end of", value=a.opening_balance_date)
            active = st.checkbox("Active", value=a.active)
            if st.form_submit_button("Save changes"):
                try:
                    opening = parse_cents(opening_text) if opening_text.strip() else None
                    with connect() as conn, conn.transaction():
                        save_account(conn, account_id=account_id, display_name=name, institution=institution,
                                     account_type=acct_type, owner_person_id=owner, last4=last4,
                                     parser_key=None if parser == NO_PARSER else parser, active=active)
                        set_opening_balance(conn, account_id, opening, opening_date if opening is not None else None)
                    finish(f"Saved {name.strip()}.")
                except ValueError as e:
                    st.error(md(e))

# ---------------------------------------------------------------------------
# New account
# ---------------------------------------------------------------------------
with st.expander("Add an account"):
    if not people:
        st.write("Add people first (seed/local.toml, then `python -m budget.seed`).")
    else:
        with st.form(f"new_account_{state.accounts_round}"):
            n1, n2 = st.columns(2)
            new_name = n1.text_input("Name", placeholder="e.g. Joint - BCU savings")
            new_institution = n2.text_input("Institution", placeholder="e.g. BCU")
            n3, n4, n5 = st.columns(3)
            new_type = n3.selectbox("Type", list(ACCOUNT_TYPES), format_func=TYPE_LABELS.get)
            new_owner = n4.selectbox("Owner", list(people), format_func=people.get)
            new_last4 = n5.text_input("Last 4 digits (optional)")
            new_parser = st.selectbox("CSV format (parser)", [NO_PARSER] + sorted(PARSERS),
                                      help="Which bank export this account's CSVs come in. None for an account "
                                           "you only enter by hand.")
            if st.form_submit_button("Add account"):
                try:
                    with connect() as conn:
                        save_account(conn, display_name=new_name, institution=new_institution, account_type=new_type,
                                     owner_person_id=new_owner, last4=new_last4,
                                     parser_key=None if new_parser == NO_PARSER else new_parser)
                    finish(f"Added {new_name.strip()}. Choose it above to set its opening balance.")
                except ValueError as e:
                    st.error(md(e))
