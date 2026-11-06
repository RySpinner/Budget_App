"""Import account CSV exports (cards now; bank accounts from phase 9) from the command line.

Phase 2 uses this to prove the import pipeline works; Phase 3 puts the same
preview / confirm / undo steps on a Streamlit page.

Usage:
    python -m budget.import_csv --accounts                      # list accounts and their ids
    python -m budget.import_csv --account 1 export.csv          # preview, then ask to confirm
    python -m budget.import_csv --account 1 export.csv --yes    # no prompt
    python -m budget.import_csv --batches                       # list past imports
    python -m budget.import_csv --undo 7                        # undo batch 7
"""

import argparse
import sys
from pathlib import Path

from budget.db import connect
from budget.importer import commit_import, preview_import, undo_import
from budget.money import format_cents
from budget.parsers.base import describe


def show_preview(p) -> None:
    print(f"\n{p.filename} -> {p.account_name}")
    print(f"  rows in file:   {p.rows_in_file}")
    print(f"  new:            {len(p.new_rows)}")
    print(f"  duplicate:      {len(p.duplicate_rows)}")
    print(f"  excluded:       {len(p.excluded_rows)}")
    print(f"  uncategorized:  {p.uncategorized_count}")
    print(f"  transfers:      {p.transfer_count}  (e.g. card payments; matched to the other account in phase 10)")
    if p.already_imported_batch_ids:
        print(f"  NOTE: this exact file was already imported (batch {', '.join(map(str, p.already_imported_batch_ids))})")
    if p.new_rows:
        print("\n  New rows:")
        for n in p.new_rows:
            r = n.parsed
            label = describe(n.kind, r.amount_cents, r.reason)
            print(f"    {r.txn_date or r.posted_date}  {format_cents(r.amount_cents):>11}  {label:<24}  {n.merchant:<30}  {r.raw_description}")
    if p.excluded_rows:
        print("\n  Excluded:")
        for r in p.excluded_rows:
            print(f"    {r.txn_date or r.posted_date}  {format_cents(r.amount_cents):>11}  [{r.reason}]  {r.raw_description}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--account", "--card", dest="account", type=int, help="account id the files belong to")
    ap.add_argument("--yes", action="store_true", help="import without asking")
    ap.add_argument("--accounts", "--cards", dest="accounts", action="store_true", help="list accounts")
    ap.add_argument("--batches", action="store_true", help="list import batches")
    ap.add_argument("--undo", type=int, metavar="BATCH_ID", help="undo an import batch")
    args = ap.parse_args()

    with connect() as conn:
        if args.accounts:
            for row in conn.execute(
                "SELECT id, display_name, account_type, parser_key, active FROM accounts ORDER BY id"
            ):
                parser = row[3] or "(entered by hand)"
                print(f"{row[0]:>3}  {row[1]:<35} {row[2]:<12} parser={parser}{'' if row[4] else '  (inactive)'}")
            return

        if args.batches:
            rows = conn.execute(
                """SELECT b.id, b.imported_at::date, c.display_name, b.filename, b.rows_inserted,
                          b.rows_duplicate, b.rows_excluded, b.reverted_at
                     FROM import_batches b JOIN accounts c ON c.id = b.account_id ORDER BY b.id"""
            ).fetchall()
            for r in rows:
                status = f"UNDONE {r[7]:%Y-%m-%d}" if r[7] else ""
                print(f"{r[0]:>4}  {r[1]}  {r[2]:<25} {r[3]:<30} +{r[4]} dup={r[5]} excl={r[6]} {status}")
            return

        if args.undo is not None:
            deleted = undo_import(conn, args.undo)
            print(f"Batch {args.undo} undone; {deleted} transactions deleted.")
            return

        if not args.files or args.account is None:
            ap.error("give --account ID and one or more CSV files (or --accounts / --batches / --undo)")

        for path in args.files:
            preview = preview_import(conn, args.account, path.name, path.read_bytes())
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
