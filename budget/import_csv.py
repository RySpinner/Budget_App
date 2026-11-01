"""Import card CSV exports from the command line.

Phase 2 uses this to prove the import pipeline works; Phase 3 puts the same
preview / confirm / undo steps on a Streamlit page.

Usage:
    python -m budget.import_csv --cards                         # list cards and their ids
    python -m budget.import_csv --card 1 export.csv             # preview, then ask to confirm
    python -m budget.import_csv --card 1 export.csv --yes       # no prompt
    python -m budget.import_csv --batches                       # list past imports
    python -m budget.import_csv --undo 7                        # undo batch 7
"""

import argparse
import sys
from pathlib import Path

from budget.db import connect
from budget.importer import commit_import, preview_import, undo_import
from budget.money import format_cents


def show_preview(p) -> None:
    print(f"\n{p.filename} -> {p.card_name}")
    print(f"  rows in file:   {p.rows_in_file}")
    print(f"  new:            {len(p.new_rows)}")
    print(f"  duplicate:      {len(p.duplicate_rows)}")
    print(f"  excluded:       {len(p.excluded_rows)}")
    print(f"  uncategorized:  {p.uncategorized_count}")
    if p.already_imported_batch_ids:
        print(f"  NOTE: this exact file was already imported (batch {', '.join(map(str, p.already_imported_batch_ids))})")
    if p.new_rows:
        print("\n  New rows:")
        for n in p.new_rows:
            r = n.parsed
            print(f"    {r.txn_date or r.posted_date}  {format_cents(r.amount_cents):>11}  {n.merchant:<30}  {r.raw_description}")
    if p.excluded_rows:
        print("\n  Excluded:")
        for r in p.excluded_rows:
            print(f"    {r.txn_date or r.posted_date}  {format_cents(r.amount_cents):>11}  [{r.exclude_reason}]  {r.raw_description}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--card", type=int, help="card id the files belong to")
    ap.add_argument("--yes", action="store_true", help="import without asking")
    ap.add_argument("--cards", action="store_true", help="list cards")
    ap.add_argument("--batches", action="store_true", help="list import batches")
    ap.add_argument("--undo", type=int, metavar="BATCH_ID", help="undo an import batch")
    args = ap.parse_args()

    with connect() as conn:
        if args.cards:
            for row in conn.execute("SELECT id, display_name, parser_key, active FROM cards ORDER BY id"):
                print(f"{row[0]:>3}  {row[1]:<35} parser={row[2]}{'' if row[3] else '  (inactive)'}")
            return

        if args.batches:
            rows = conn.execute(
                """SELECT b.id, b.imported_at::date, c.display_name, b.filename, b.rows_inserted,
                          b.rows_duplicate, b.rows_excluded, b.reverted_at
                     FROM import_batches b JOIN cards c ON c.id = b.card_id ORDER BY b.id"""
            ).fetchall()
            for r in rows:
                status = f"UNDONE {r[7]:%Y-%m-%d}" if r[7] else ""
                print(f"{r[0]:>4}  {r[1]}  {r[2]:<25} {r[3]:<30} +{r[4]} dup={r[5]} excl={r[6]} {status}")
            return

        if args.undo is not None:
            deleted = undo_import(conn, args.undo)
            print(f"Batch {args.undo} undone; {deleted} transactions deleted.")
            return

        if not args.files or args.card is None:
            ap.error("give --card ID and one or more CSV files (or --cards / --batches / --undo)")

        for path in args.files:
            preview = preview_import(conn, args.card, path.name, path.read_bytes())
            show_preview(preview)
            if not preview.new_rows:
                print("\nNothing new to import; skipped.")
                continue
            if not args.yes and input("\nImport these rows? [y/N] ").strip().lower() != "y":
                print("Skipped.")
                continue
            batch_id = commit_import(conn, preview)
            print(f"Imported as batch {batch_id}.")


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        sys.exit(f"Error: {e}")
