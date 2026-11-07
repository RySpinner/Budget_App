"""Rules page: see, edit, add and delete rules, then re-run them.

A rule sets a category, or marks rows as a transfer between our accounts
(optionally naming the other account). Rules are tried in priority order
(lower number first); the first match wins. Rows categorized or classified
by hand are never changed by rules.
"""

import pandas as pd
import streamlit as st

from budget.categories import subcategory_choices
from app.ui import md
from budget.db import connect
from budget.money import format_cents, parse_cents
from budget.rules import MATCH_TYPES, apply_rules, create_rule, validate_rule

TRANSFER_LABEL = "↔ Transfer between our accounts"
NO_ACCOUNT = "—"

state = st.session_state
state.setdefault("rules_round", 0)
state.setdefault("rules_flash", None)

st.title("Rules")

if state.rules_flash:
    st.success(md(state.rules_flash))
    state.rules_flash = None

with connect() as conn:
    choices = subcategory_choices(conn, include_inactive=True)
    accounts = dict(conn.execute("SELECT id, display_name FROM accounts ORDER BY display_name").fetchall())
    rules = conn.execute(
        """
        SELECT r.id, r.active, r.priority, r.match_type, r.pattern, r.category_id, r.merchant_name,
               r.account_id, r.min_amount_cents, r.max_amount_cents,
               (SELECT count(*) FROM transactions t WHERE t.rule_id = r.id) AS used,
               r.is_transfer, r.counterpart_account_id
          FROM rules r
         ORDER BY r.priority, r.id
        """
    ).fetchall()

label_to_id = {label: cat_id for cat_id, label in choices.items()}
sets_options = list(label_to_id) + [TRANSFER_LABEL]
ANY_ACCOUNT = "Any account"
account_label_to_id = {ANY_ACCOUNT: None} | {name: aid for aid, name in accounts.items()}
other_label_to_id = {NO_ACCOUNT: None} | {name: aid for aid, name in accounts.items()}


def rule_target(sets: str | None, account_id: int | None, other: str | None) -> dict:
    """Turn the "Sets" and "Other account" choices into rule columns, checking they fit together."""
    if sets is None:
        raise ValueError("Choose a category or the transfer option")
    counterpart = other_label_to_id.get(other or NO_ACCOUNT)
    if sets == TRANSFER_LABEL:
        if counterpart is not None and counterpart == account_id:
            raise ValueError("The other account can't be the account the rule applies to")
        return {"category_id": None, "is_transfer": True, "counterpart_account_id": counterpart}
    if counterpart is not None:
        raise ValueError("'Other account' is only for transfer rules")
    return {"category_id": label_to_id[sets], "is_transfer": False, "counterpart_account_id": None}


def optional_cents(text) -> int | None:
    text = str(text or "").strip()
    return parse_cents(text) if text else None


def run_rules_message(r) -> str:
    return (f"Rules re-run: {r.categorized} newly sorted, {r.changed} changed, "
            f"{r.uncategorized} sent back to Review.")


# ---------------------------------------------------------------------------
# Existing rules
# ---------------------------------------------------------------------------
if not rules:
    st.write("No rules yet. Create them from the Review page or with the form below.")
else:
    table = pd.DataFrame(
        [
            {
                "id": r[0],
                "Active": r[1],
                "Priority": r[2],
                "Match": r[3],
                "Pattern": r[4],
                "Sets": TRANSFER_LABEL if r[11] else choices.get(r[5]),
                "Other account": accounts.get(r[12], NO_ACCOUNT),
                "Merchant name": r[6] or "",
                "Account": accounts.get(r[7], ANY_ACCOUNT),
                "Min amount": format_cents(r[8]) if r[8] is not None else "",
                "Max amount": format_cents(r[9]) if r[9] is not None else "",
                "Used by": r[10],
                "Delete": False,
            }
            for r in rules
        ]
    )
    edited = st.data_editor(
        table,
        key=f"rules_editor_{state.rules_round}",
        hide_index=True,
        width="stretch",
        disabled=["Used by"],
        column_config={
            "id": None,
            "Priority": st.column_config.NumberColumn(step=1, format="%d", help="Lower runs first"),
            "Match": st.column_config.SelectboxColumn(options=list(MATCH_TYPES), required=True),
            "Sets": st.column_config.SelectboxColumn(options=sets_options, required=True,
                                                     help="A category, or: this is a transfer between our accounts"),
            "Other account": st.column_config.SelectboxColumn(
                options=list(other_label_to_id), required=True,
                help="Transfer rules only: the account on the other side, if known (helps matching)"),
            "Merchant name": st.column_config.TextColumn(help="Optional display name to apply"),
            "Account": st.column_config.SelectboxColumn(options=list(account_label_to_id), required=True),
            "Min amount": st.column_config.TextColumn(help="Optional, e.g. 10.00"),
            "Max amount": st.column_config.TextColumn(help="Optional, e.g. 200.00"),
            "Used by": st.column_config.NumberColumn(help="Transactions currently categorized by this rule"),
        },
    )

    if st.button("Save changes and re-run rules", type="primary"):
        errors, updates, deletes = [], [], []
        for _, row in edited.iterrows():
            rule_id = int(row["id"])
            if row["Delete"]:
                deletes.append(rule_id)
                continue
            try:
                validate_rule(row["Match"], str(row["Pattern"] or ""))
                low, high = optional_cents(row["Min amount"]), optional_cents(row["Max amount"])
                if low is not None and high is not None and low > high:
                    raise ValueError("Min amount is more than max amount")
                account_id = account_label_to_id[row["Account"]]
                target = rule_target(row["Sets"], account_id, row["Other account"])
                updates.append((
                    bool(row["Active"]), int(row["Priority"]), row["Match"], str(row["Pattern"]).strip(),
                    target["category_id"], target["is_transfer"], target["counterpart_account_id"],
                    str(row["Merchant name"] or "").strip() or None,
                    account_id, low, high, rule_id,
                ))
            except (ValueError, KeyError, TypeError) as e:
                errors.append(f"Rule with pattern {row['Pattern']!r}: {e}")

        if errors:
            for e in errors:
                st.error(md(e))
        else:
            with connect() as conn:
                with conn.transaction():
                    for u in updates:
                        conn.execute(
                            """
                            UPDATE rules SET active = %s, priority = %s, match_type = %s, pattern = %s,
                                   category_id = %s, is_transfer = %s, counterpart_account_id = %s,
                                   merchant_name = %s, account_id = %s,
                                   min_amount_cents = %s, max_amount_cents = %s
                             WHERE id = %s
                            """,
                            u,
                        )
                    if deletes:
                        conn.execute("DELETE FROM rules WHERE id = ANY(%s)", (deletes,))
                result = apply_rules(conn)
            state.rules_flash = f"Saved. {len(deletes)} deleted. " + run_rules_message(result)
            state.rules_round += 1
            st.rerun()

# ---------------------------------------------------------------------------
# Add a rule
# ---------------------------------------------------------------------------
with st.expander("Add a rule"):
    with st.form("add_rule", clear_on_submit=True):
        c1, c2 = st.columns([3, 1])
        pattern = c1.text_input("Pattern", help="Matched against the bank description, ignoring case")
        match_type = c2.selectbox("Match", MATCH_TYPES)
        sets = st.selectbox("Sets", sets_options, index=None, placeholder="Choose a category, or transfer")
        c5, c6 = st.columns(2)
        account = c5.selectbox("Only in account", list(account_label_to_id))
        other = c6.selectbox("Other account (transfers only)", list(other_label_to_id))
        c3, c4 = st.columns([3, 1])
        merchant_name = c3.text_input("Merchant name (optional)")
        priority = c4.number_input("Priority", value=100, step=1)
        if st.form_submit_button("Add rule and run it"):
            try:
                account_id = account_label_to_id[account]
                target = rule_target(sets, account_id, other)
                with connect() as conn:
                    create_rule(conn, pattern, target["category_id"], match_type=match_type,
                                priority=int(priority), merchant_name=merchant_name.strip() or None,
                                account_id=account_id, is_transfer=target["is_transfer"],
                                counterpart_account_id=target["counterpart_account_id"])
                    conn.commit()
                    result = apply_rules(conn)
                state.rules_flash = "Rule added. " + run_rules_message(result)
                state.rules_round += 1
                st.rerun()
            except ValueError as e:
                st.error(md(str(e)))

if st.button("Re-run all rules"):
    with connect() as conn:
        state.rules_flash = run_rules_message(apply_rules(conn))
    st.rerun()
