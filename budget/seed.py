"""Load starting data: categories (tracked in git) plus people and cards
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


def seed_categories(conn: psycopg.Connection, data: dict) -> int:
    """Insert missing categories. Returns how many rows were added."""
    added = 0
    for top_order, top in enumerate(data.get("category", []), start=1):
        subcategories = list(top.get("subcategories", []))
        if GENERAL not in subcategories:
            subcategories.append(GENERAL)

        parent_id, was_added = _get_or_create_category(conn, top["name"], None, top_order * 10)
        added += was_added
        for sub_order, sub_name in enumerate(subcategories, start=1):
            _, was_added = _get_or_create_category(conn, sub_name, parent_id, sub_order * 10)
            added += was_added
    return added


def _get_or_create_category(conn, name: str, parent_id: int | None, sort_order: int) -> tuple[int, bool]:
    row = conn.execute(
        "SELECT id FROM categories WHERE name = %s AND parent_id IS NOT DISTINCT FROM %s",
        (name, parent_id),
    ).fetchone()
    if row:
        return row[0], False
    row = conn.execute(
        "INSERT INTO categories (name, parent_id, sort_order) VALUES (%s, %s, %s) RETURNING id",
        (name, parent_id, sort_order),
    ).fetchone()
    return row[0], True


def seed_people_and_cards(conn: psycopg.Connection, data: dict) -> tuple[int, int, list[str]]:
    """Insert missing people and cards.

    Returns (people_added, cards_added, warnings). Cards that already exist
    are never changed; if local.toml disagrees with the database, a warning
    explains the difference so it can be fixed in the database instead.
    """
    people_added = 0
    for person in data.get("person", []):
        cur = conn.execute(
            "INSERT INTO people (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
            (person["name"],),
        )
        people_added += cur.rowcount

    cards_added = 0
    warnings = []
    for card in data.get("card", []):
        owner = conn.execute("SELECT id FROM people WHERE name = %s", (card["owner"],)).fetchone()
        if owner is None:
            raise ValueError(f"Card {card['display_name']!r}: owner {card['owner']!r} is not a known person")
        wanted = {
            "issuer": card["issuer"],
            "last4": card.get("last4"),
            "owner": card["owner"],
            "parser_key": card["parser_key"],
        }

        existing = conn.execute(
            """
            SELECT c.id, c.issuer, c.last4, p.name, c.parser_key
              FROM cards c JOIN people p ON p.id = c.owner_person_id
             WHERE c.display_name = %s
            """,
            (card["display_name"],),
        ).fetchone()

        if existing is None:
            conn.execute(
                """
                INSERT INTO cards (display_name, issuer, last4, owner_person_id, parser_key)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (card["display_name"], wanted["issuer"], wanted["last4"], owner[0], wanted["parser_key"]),
            )
            cards_added += 1
            continue

        card_id, *values = existing
        current = dict(zip(["issuer", "last4", "owner", "parser_key"], values))
        for field, value in wanted.items():
            if (current[field] or None) != (value or None):
                warnings.append(
                    f"Card {card['display_name']!r} (id {card_id}) already exists with "
                    f"{field} = {current[field]!r}; local.toml says {value!r}. Not changed: "
                    f"seeding never edits existing rows, so update it in the database."
                )
    return people_added, cards_added, warnings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--local", type=Path, default=LOCAL_FILE, help="people/cards file (default: seed/local.toml)")
    args = parser.parse_args()

    with connect() as conn:
        added = seed_categories(conn, load_toml(CATEGORIES_FILE))
        print(f"categories: {added} added")

        if args.local.exists():
            people, cards, warnings = seed_people_and_cards(conn, load_toml(args.local))
            print(f"people: {people} added, cards: {cards} added")
            for warning in warnings:
                print(f"WARNING: {warning}")
        else:
            print(f"No {args.local} found; skipped people and cards (see seed/local.example.toml).")


if __name__ == "__main__":
    main()
