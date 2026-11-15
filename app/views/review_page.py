"""Review page: the rows that need a decision, in three tabs.

- Uncategorized: pick a category for any rows you want to handle now (leave
  the rest blank), tick "Save as rule" to sort similar rows automatically
  from now on, then press Save. Choosing "Transfer between our accounts"
  makes the row a transfer. A rule saved from it marks similar rows *in the
  same account* as transfers, and can name the account on the other side
  ("Other account"), which helps the transfer matcher.
- Unmatched transfers: transfer rows whose other leg wasn't found
  automatically. Link each to the other account's row, create the other
  side in an account without a CSV (e.g. Cash), or say it isn't a transfer
  by giving it a category. Links made recently can be undone here.
- Needs care: ATM, Venmo/Zelle and check rows (SPEC section 5a). Rules never
  sort these, so each gets a category (and ideally a note) by hand.
"""

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from app.ui import md
from budget.categories import subcategory_choices
from budget.db import connect
from budget.money import format_cents
from budget.rules import categorize_manually, mark_transfer_manually, suggest_pattern
from budget.transactions import set_note
from budget.transfers import (
    accounts_without_csv, candidates, generate_leg, link, recent_links, unlink_by_hand, unmatched_legs,
)

TRANSFER_LABEL = "↔ Transfer between our accounts"
NO_ACCOUNT = "—"
LEAVE = "Leave for now"
RECENT_DAYS = 60
MATCHED_BY = {"auto": "automatically", "rule": "by a rule", "manual": "by hand"}


def plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def short_date(d: date) -> str:
    return f"{d:%b} {d.day}, {d:%Y}"


state = st.session_state
state.setdefault("review_round", 0)   # bump to reset the editors after saving
state.setdefault("review_flash", None)

st.title("Review")

if state.review_flash:
    st.success(md(state.review_flash))
    state.review_flash = None


def finish(message: str) -> None:
    """Show a message after the rerun and reset the editors."""
    state.review_flash = message
    state.review_round += 1
    st.rerun()


with connect() as conn:
    choices = subcategory_choices(conn)
    accounts = dict(conn.execute("SELECT id, display_name FROM accounts WHERE active ORDER BY display_name"))
    no_csv_accounts = accounts_without_csv(conn)
    uncategorized = conn.execute(
        """
        SELECT t.id, t.effective_date, t.amount_cents, a.display_name, t.merchant, t.raw_description,
               t.account_id
          FROM transactions t
          JOIN accounts a ON a.id = t.account_id
         WHERE t.category_id IS NULL
           AND (t.kind IS NULL OR t.kind IN ('expense', 'income'))  -- transfers: Unmatched transfers tab
           AND t.needs_care IS NULL                 -- see Needs care
           AND NOT EXISTS (SELECT 1 FROM splits s WHERE s.transaction_id = t.id)  -- split lines carry the categories
         ORDER BY t.effective_date DESC, t.id
        """
    ).fetchall()
    care = conn.execute(
        """
        SELECT t.id, t.effective_date, t.amount_cents, a.display_name, t.merchant, t.raw_description,
               t.needs_care, t.note
          FROM transactions t
          JOIN accounts a ON a.id = t.account_id
         WHERE t.needs_care IS NOT NULL
           AND t.category_id IS NULL
           AND (t.kind IS NULL OR t.kind IN ('expense', 'income'))
           AND NOT EXISTS (SELECT 1 FROM splits s WHERE s.transaction_id = t.id)
         ORDER BY t.effective_date DESC, t.id
        """
    ).fetchall()
    legs = unmatched_legs(conn)
    leg_candidates = {leg.id: candidates(conn, leg.id, manual=True) for leg in legs}
    links = recent_links(conn, date.today() - timedelta(days=RECENT_DAYS))

label_to_id = {label: cat_id for cat_id, label in choices.items()}
category_options = list(label_to_id) + [TRANSFER_LABEL]
account_label_to_id = {name: aid for aid, name in accounts.items()}

tab_uncat, tab_transfers, tab_care = st.tabs([
    f"Uncategorized ({len(uncategorized)})",
    f"Unmatched transfers ({len(legs)})",
    f"Needs care ({len(care)})",
])

# ---------------------------------------------------------------------------
# Uncategorized
# ---------------------------------------------------------------------------
with tab_uncat:
    if not uncategorized:
        st.write("Nothing to review: every transaction has a category.")
    else:
        st.caption(
            f"{len(uncategorized)} uncategorized. Choose a category (spending or income) for the rows you want "
            f"to handle, or **{TRANSFER_LABEL}** for money moving between your own accounts. Blank rows stay "
            "in the queue. A saved rule uses the pattern shown (matched anywhere in the bank description, "
            "ignoring case) and also applies to other uncategorized rows. For a transfer, the rule only "
            "applies to the same account; **Other account** (optional) says where the money went or came from."
        )
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
                for r in uncategorized
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
        if st.button(f"Save {plural(len(chosen), 'categorized row')}", type="primary", disabled=chosen.empty,
                     key="save_uncategorized"):
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
                        rule_id, others = categorize_manually(conn, int(row["id"]), label_to_id[row["Category"]],
                                                              pattern)
                    rules_made += rule_id is not None
                    also_categorized += others

            message = f"Saved {plural(len(chosen), 'row')}."
            if rules_made:
                message += (f" Created {plural(rules_made, 'rule')}, which sorted "
                            f"{plural(also_categorized, 'other row')}.")
            finish(message)

# ---------------------------------------------------------------------------
# Unmatched transfers
# ---------------------------------------------------------------------------
NOT_TRANSFER = "Not a transfer: "


def other_side_options(leg) -> dict[str, tuple[str, int | None]]:
    """Choices for one unmatched leg: label -> (action, id)."""
    options: dict[str, tuple[str, int | None]] = {LEAVE: ("leave", None)}
    for c in leg_candidates[leg.id]:
        now = "" if c.kind in ("transfer", None) else f" (now {'spending' if c.kind == 'expense' else c.kind})"
        options[f"{short_date(c.effective_date)} · {c.account} · {format_cents(c.amount_cents)} · "
                f"{c.merchant or ''}{now}"] = ("link", c.id)
    for account_id, name in no_csv_accounts.items():
        if account_id != leg.account_id:
            options[f"New entry in {name}"] = ("generate", account_id)
    for label, cat_id in label_to_id.items():
        options[NOT_TRANSFER + label] = ("category", cat_id)
    return options


with tab_transfers:
    if not legs:
        st.write("Nothing to review: every transfer is linked to its other side.")
    else:
        st.caption(
            "Transfer rows whose other side wasn't found automatically (no match, or more than one). For each, "
            "pick the other account's row (same amount, opposite sign, up to 10 days apart), or **New entry in** "
            "an account without a CSV (e.g. Cash) to create its side, or a category if it isn't a transfer "
            "after all (e.g. money sent to someone outside the household). Then press Save."
        )
        picks = {}
        for leg in legs:
            options = other_side_options(leg)
            with st.container(border=True):
                hint = (f" · rule says the other account is **{accounts.get(leg.counterpart_account_id)}**"
                        if leg.counterpart_account_id else "")
                st.markdown(md(f"**{short_date(leg.effective_date)} · {leg.account} · "
                               f"{format_cents(leg.amount_cents)}** · {leg.merchant or ''}{hint}"))
                st.caption(md(leg.raw_description))
                if not leg_candidates[leg.id]:
                    st.caption("No row with the opposite amount in another account within 10 days. If the other "
                               "account isn't imported yet, leave this for now.")
                picks[leg.id] = options[st.selectbox(
                    "Other side", list(options), key=f"other_side_{leg.id}_{state.review_round}",
                )]

        to_do = {leg_id: pick for leg_id, pick in picks.items() if pick[0] != "leave"}
        if st.button(f"Save {plural(len(to_do), 'choice')}", type="primary", disabled=not to_do,
                     key="save_transfers"):
            done, problems = 0, []
            with connect() as conn:
                for leg_id, (action, target) in to_do.items():
                    try:
                        if action == "link":
                            link(conn, leg_id, target, matched_by="manual")
                        elif action == "generate":
                            generate_leg(conn, leg_id, target, matched_by="manual")
                        else:
                            categorize_manually(conn, leg_id, target)
                        done += 1
                    except ValueError as e:   # e.g. two rows picked the same partner
                        problems.append(str(e))
            message = f"Saved {plural(done, 'choice')}."
            if problems:
                message += " Not saved: " + "; ".join(problems)
            finish(message)

    if links:
        with st.expander(f"Transfers linked in the last {RECENT_DAYS} days ({len(links)})"):
            st.caption("If two rows were linked by mistake, unlink them. They won't be linked automatically "
                       "again; a row the app created for an account without a CSV is deleted.")
            for t in links:
                c1, c2 = st.columns([5, 1])
                c1.markdown(md(f"{short_date(t.effective_date)} · **{format_cents(t.amount_cents)}** from "
                               f"{t.from_account} to {t.to_account} · {t.description} · "
                               f"linked {MATCHED_BY.get(t.matched_by, t.matched_by)}"))
                if c2.button("Unlink", key=f"unlink_{t.transfer_id}"):
                    with connect() as conn:
                        unlink_by_hand(conn, t.transfer_id)
                    finish("Unlinked. Both rows are back in Review.")

# ---------------------------------------------------------------------------
# Needs care
# ---------------------------------------------------------------------------
with tab_care:
    if not care:
        st.write("Nothing needs care right now.")
    else:
        st.caption(
            "ATM withdrawals, Venmo/Zelle payments and checks can be spending, income, a transfer or money "
            "between the two of you, so rules never sort them. Choose a category (and add a note saying what it "
            f"was), or **{TRANSFER_LABEL}** and then link it under Unmatched transfers (an ATM withdrawal is "
            "usually a transfer to Cash)."
        )
        care_table = pd.DataFrame(
            [
                {
                    "id": r[0],
                    "Date": r[1],
                    "Amount": format_cents(r[2]),
                    "Account": r[3],
                    "Why": r[6],
                    # The full bank text: merchant cleanup would drop a check number.
                    "Bank description": r[5],
                    "Category": None,
                    "Note": r[7] or "",
                }
                for r in care
            ]
        )
        care_edited = st.data_editor(
            care_table,
            key=f"care_editor_{state.review_round}",
            hide_index=True,
            width="stretch",
            disabled=["Date", "Amount", "Account", "Why", "Bank description"],
            column_config={
                "id": None,
                "Category": st.column_config.SelectboxColumn(options=category_options, width="medium"),
                "Note": st.column_config.TextColumn(width="medium", help="What it was, e.g. who the check went to"),
            },
        )
        care_chosen = care_edited[care_edited["Category"].notna()]
        if st.button(f"Save {plural(len(care_chosen), 'row')}", type="primary", disabled=care_chosen.empty,
                     key="save_care"):
            with connect() as conn:
                for _, row in care_chosen.iterrows():
                    txn_id = int(row["id"])
                    if row["Category"] == TRANSFER_LABEL:
                        mark_transfer_manually(conn, txn_id)
                    else:
                        categorize_manually(conn, txn_id, label_to_id[row["Category"]])
                    set_note(conn, txn_id, row["Note"])
            finish(f"Saved {plural(len(care_chosen), 'row')}.")
