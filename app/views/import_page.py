"""Import page: upload CSVs, preview, confirm; list past imports with undo.

All the real work happens in budget/importer.py; this file only lays out
the page. Nothing is written to the database until "Import" is clicked.
"""

import pandas as pd
import streamlit as st

from budget.categories import subcategory_choices
from app.ui import md
from budget.db import connect
from budget.importer import commit_import, detect_card, preview_import, undo_import
from budget.money import format_cents

state = st.session_state
state.setdefault("uploader_round", 0)     # bump to clear the file uploader
state.setdefault("imported", {})          # uploaded file id -> batch id
state.setdefault("confirm_undo", None)    # batch id waiting for confirmation
state.setdefault("flash", None)           # one-off message shown after a rerun

st.title("Import")

if state.flash:
    st.success(md(state.flash))
    state.flash = None

try:
    with connect() as conn:
        cards = conn.execute(
            "SELECT id, display_name FROM cards WHERE active ORDER BY display_name"
        ).fetchall()
        category_names = subcategory_choices(conn, include_inactive=True)
except Exception as e:  # e.g. database not reachable
    st.error(md(f"Could not connect to the database: {e}"))
    st.stop()

card_names = dict(cards)

if not cards:
    st.warning(md("No cards set up yet. Add them to seed/local.toml and run `python -m budget.seed`."))
    st.stop()


def rows_table(rows) -> pd.DataFrame:
    """Rows for display. Amounts are shown as text, so no floats are involved.

    `rows` holds (ParsedRow, merchant, matching rule or None).
    """
    return pd.DataFrame(
        [
            {
                "Date": r.txn_date or r.posted_date,
                "Amount": format_cents(r.amount_cents),
                "Merchant": merchant,
                "Type": r.exclude_reason or r.kind,
                "Category (from rules)": category_names.get(rule.category_id) if rule else "",
                "Bank description": r.raw_description,
            }
            for r, merchant, rule in rows
        ]
    )


# ---------------------------------------------------------------------------
# Upload and preview
# ---------------------------------------------------------------------------
uploaded = st.file_uploader(
    "Drop card CSV exports here",
    type=["csv"],
    accept_multiple_files=True,
    key=f"uploader_{state.uploader_round}",
)

for f in uploaded or []:
    data = f.getvalue()
    with st.container(border=True):
        st.subheader(f.name)

        if f.file_id in state.imported:
            st.success(md(f"Imported as batch {state.imported[f.file_id]}."))
            continue

        with connect() as conn:
            detected = detect_card(conn, data)
        ids = list(card_names)
        card_id = st.selectbox(
            "Card",
            options=ids,
            index=ids.index(detected) if detected in ids else None,
            format_func=card_names.get,
            placeholder="Choose which card this export is from",
            key=f"card_{f.file_id}",
        )
        if detected is not None and card_id == detected:
            st.caption("Card detected from the file.")
        if card_id is None:
            continue

        try:
            with connect() as conn:
                preview = preview_import(conn, card_id, f.name, data)
        except ValueError as e:
            st.error(md(str(e)))
            continue

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("New", len(preview.new_rows))
        c2.metric("Duplicate", len(preview.duplicate_rows))
        c3.metric("Excluded", len(preview.excluded_rows))
        c4.metric("Uncategorized", preview.uncategorized_count)

        if preview.already_imported_batch_ids:
            batches = ", ".join(map(str, preview.already_imported_batch_ids))
            st.info(md(f"This exact file was already imported (batch {batches}). Duplicates will be skipped."))

        if preview.new_rows:
            st.markdown("**New rows**")
            st.dataframe(
                rows_table([(n.parsed, n.merchant, n.rule) for n in preview.new_rows]),
                hide_index=True, width="stretch",
            )
        else:
            st.write("Nothing new in this file.")

        if preview.excluded_rows:
            with st.expander(f"Excluded rows ({len(preview.excluded_rows)}): not recorded as spending"):
                st.dataframe(rows_table([(r, "", None) for r in preview.excluded_rows]), hide_index=True, width="stretch")
        if preview.duplicate_rows:
            with st.expander(f"Duplicate rows ({len(preview.duplicate_rows)}): already in the database"):
                st.dataframe(
                    rows_table([(n.parsed, n.merchant, None) for n in preview.duplicate_rows]),
                    hide_index=True, width="stretch",
                )

        if st.button(
            f"Import {len(preview.new_rows)} new rows",
            type="primary",
            disabled=not preview.new_rows,
            key=f"import_{f.file_id}",
        ):
            with connect() as conn:
                batch_id = commit_import(conn, preview)
            state.imported[f.file_id] = batch_id
            st.rerun()

if state.imported and st.button("Clear uploaded files"):
    state.imported = {}
    state.uploader_round += 1
    st.rerun()

# ---------------------------------------------------------------------------
# Past imports and undo
# ---------------------------------------------------------------------------
st.header("Past imports")

with connect() as conn:
    batches = conn.execute(
        """
        SELECT b.id, b.imported_at, c.display_name, b.filename, b.rows_in_file,
               b.rows_inserted, b.rows_duplicate, b.rows_excluded, b.reverted_at
          FROM import_batches b JOIN cards c ON c.id = b.card_id
         ORDER BY b.id DESC
        """
    ).fetchall()

if not batches:
    st.write("No imports yet.")
else:
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Batch": b[0],
                    "Imported": b[1].strftime("%Y-%m-%d %H:%M"),
                    "Card": b[2],
                    "File": b[3],
                    "Rows": b[4],
                    "Added": b[5],
                    "Duplicate": b[6],
                    "Excluded": b[7],
                    "Status": f"Undone {b[8]:%Y-%m-%d}" if b[8] else "Active",
                }
                for b in batches
            ]
        ),
        hide_index=True, width="stretch",
    )

    undoable = {b[0]: f"Batch {b[0]}: {b[3]} ({b[2]}, {b[5]} rows)" for b in batches if b[8] is None}
    if undoable:
        col1, col2 = st.columns([3, 1], vertical_alignment="bottom")
        choice = col1.selectbox("Undo an import", options=list(undoable), format_func=undoable.get, index=None,
                                placeholder="Choose a batch")
        if col2.button("Undo…", disabled=choice is None):
            state.confirm_undo = choice

    if state.confirm_undo is not None:
        batch_id = state.confirm_undo
        st.warning(md(
            f"Undo batch {batch_id}? This deletes every transaction it added, including any "
            "categories, notes or splits you have set on them since. The archived CSV is kept, "
            "so the file can be imported again."
        ))
        yes, no, _ = st.columns([1, 1, 4])
        if yes.button("Yes, undo it", type="primary"):
            with connect() as conn:
                deleted = undo_import(conn, batch_id)
            state.confirm_undo = None
            state.flash = f"Batch {batch_id} undone; {deleted} transactions deleted."
            st.rerun()
        if no.button("Cancel"):
            state.confirm_undo = None
            st.rerun()
