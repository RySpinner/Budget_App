"""Load starting data: categories (tracked in git) plus people and accounts
(from the untracked seed/local.toml).

Safe to run repeatedly: it only inserts rows that are missing (matched by
name) and never updates or deletes anything already in the database.

Usage:
    python -m budget.seed
    python -m budget.seed --local path/to/other.toml
"""

import argparse
import tomllib
from pathlib import Path

import psycopg

from budget.config import PROJECT_ROOT
from budget.db import connect

CATEGORIES_FILE = PROJECT_ROOT / "seed" / "categories.toml"
LOCAL_FILE = PROJECT_ROOT / "seed" / "local.toml"
GENERAL = "General"


def load_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


ACCOUNT_CLASS = {
    "checking": "asset", "savings": "asset", "cash": "asset", "brokerage": "asset", "retirement": "asset",
    "credit_card": "liability", "loan": "liability",
}


def seed_categories(conn: psycopg.Connection, data: dict) -> int:
    """Insert missing categories. Returns how many rows were added.

    Each top-level entry may say kind = "income"; the default is "expense".
    Subcategories always share their top-level category's kind.
    """
    added = 0
    for top_order, top in enumerate(data.get("category", []), start=1):
        subcategories = list(top.get("subcategories", []))
        if GENERAL not in subcategories:
            subcategories.append(GENERAL)

        kind = top.get("kind", "expense")
        parent_id, was_added = _get_or_create_category(conn, top["name"], None, top_order * 10, kind)
        added += was_added
        for sub_order, sub_name in enumerate(subcategories, start=1):
            _, was_added = _get_or_create_category(conn, sub_name, parent_id, sub_order * 10, kind)
            added += was_added
    return added


def _get_or_create_category(conn, name: str, parent_id: int | None, sort_order: int, kind: str) -> tuple[int, bool]:
    row = conn.execute(
        "SELECT id FROM categories WHERE name = %s AND parent_id IS NOT DISTINCT FROM %s",
        (name, parent_id),
    ).fetchone()
    if row:
        return row[0], False
    row = conn.execute(
        "INSERT INTO categories (name, parent_id, sort_order, kind) VALUES (%s, %s, %s, %s) RETURNING id",
        (name, parent_id, sort_order, kind),
    ).fetchone()
    return row[0], True


def _account_entries(data: dict) -> list[dict]:
    """[[account]] entries, plus older [[card]] entries (treated as credit cards)."""
    entries = []
    for a in data.get("account", []):
        entries.append({
            "display_name": a["display_name"], "institution": a["institution"], "type": a["type"],
            "last4": a.get("last4"), "owner": a["owner"], "parser_key": a.get("parser_key"),
        })
    for c in data.get("card", []):
        entries.append({
            "display_name": c["display_name"], "institution": c["issuer"], "type": "credit_card",
            "last4": c.get("last4"), "owner": c["owner"], "parser_key": c["parser_key"],
        })
    return entries


def seed_people_and_accounts(conn: psycopg.Connection, data: dict) -> tuple[int, int, list[str]]:
    """Insert missing people and accounts.

    Returns (people_added, accounts_added, warnings). Accounts that already
    exist are never changed; if local.toml disagrees with the database, a
    warning explains the difference so it can be fixed in the database instead.
    """
    people_added = 0
    for person in data.get("person", []):
        cur = conn.execute(
            "INSERT INTO people (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
            (person["name"],),
        )
        people_added += cur.rowcount

    accounts_added = 0
    warnings = []
    for entry in _account_entries(data):
        name = entry["display_name"]
        if entry["type"] not in ACCOUNT_CLASS:
            raise ValueError(f"Account {name!r}: type must be one of {sorted(ACCOUNT_CLASS)}")
        owner = conn.execute("SELECT id FROM people WHERE name = %s", (entry["owner"],)).fetchone()
        if owner is None:
            raise ValueError(f"Account {name!r}: owner {entry['owner']!r} is not a known person")

        existing = conn.execute(
            """
            SELECT a.id, a.institution, a.account_type, a.last4, p.name, a.parser_key
              FROM accounts a JOIN people p ON p.id = a.owner_person_id
             WHERE a.display_name = %s
            """,
            (name,),
        ).fetchone()

        if existing is None:
            conn.execute(
                """
                INSERT INTO accounts (display_name, institution, account_class, account_type, last4,
                                      owner_person_id, parser_key)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (name, entry["institution"], ACCOUNT_CLASS[entry["type"]], entry["type"], entry["last4"],
                 owner[0], entry["parser_key"]),
            )
            accounts_added += 1
            continue

        account_id, *values = existing
        current = dict(zip(["institution", "type", "last4", "owner", "parser_key"], values))
        for field in current:
            if (current[field] or None) != (entry[field] or None):
                warnings.append(
                    f"Account {name!r} (id {account_id}) already exists with "
                    f"{field} = {current[field]!r}; local.toml says {entry[field]!r}. Not changed: "
                    f"seeding never edits existing rows, so update it in the database."
                )
    return people_added, accounts_added, warnings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--local", type=Path, default=LOCAL_FILE, help="people/accounts file (default: seed/local.toml)")
    args = parser.parse_args()

    with connect() as conn:
        added = seed_categories(conn, load_toml(CATEGORIES_FILE))
        print(f"categories: {added} added")

        if args.local.exists():
            people, accounts, warnings = seed_people_and_accounts(conn, load_toml(args.local))
            print(f"people: {people} added, accounts: {accounts} added")
            for warning in warnings:
                print(f"WARNING: {warning}")
        else:
            print(f"No {args.local} found; skipped people and accounts (see seed/local.example.toml).")


if __name__ == "__main__":
    main()
