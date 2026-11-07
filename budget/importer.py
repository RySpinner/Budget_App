"""Import pipeline: CSV file -> preview -> (confirm) -> transactions.

Nothing is written to the database until commit_import() is called with a
preview, so the UI (Phase 3) and the command line can both show the counts
first and ask for confirmation.

Deduplication (SPEC section 5, step 4): each row gets a fingerprint built
from account, dates, amount, normalized description and an occurrence index,
where the nth identical row within one file gets index n. Two same-price
coffees on one day are therefore both kept, while importing an overlapping
file again inserts nothing new.
"""

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

import psycopg

from budget.config import archive_dir
from budget.merchant import clean_merchant
from budget.parsers import PARSERS, get_parser
from budget.parsers.base import ParsedRow
from budget.rules import Rule, first_match, load_rules


@dataclass
class NewRow:
    parsed: ParsedRow
    fingerprint: str
    merchant: str
    rule: Rule | None = None   # the rule that matched, if any (a category rule or a transfer rule)

    @property
    def kind(self) -> str | None:
        """What the row will be stored as: the matching rule's kind, else the parser's kind."""
        return self.rule.kind if self.rule else self.parsed.kind

    @property
    def kind_source(self) -> str | None:
        if self.kind is None:
            return None
        return "parser" if self.kind == self.parsed.kind else "rule"


@dataclass
class ImportPreview:
    account_id: int
    account_name: str
    purchaser_id: int
    filename: str
    file_bytes: bytes
    file_sha256: str
    rows_in_file: int
    new_rows: list[NewRow]
    duplicate_rows: list[NewRow]
    excluded_rows: list[ParsedRow]
    already_imported_batch_ids: list[int]   # earlier, non-undone batches of this exact file
    balances: list[tuple[date, int]] | None = None  # end-of-day balances from the file, oldest first (assets only)

    @property
    def uncategorized_count(self) -> int:
        """New rows that will need a category in Review (transfers don't have one)."""
        # A transfer rule leaves category_id None but kind "transfer", so it isn't counted.
        return sum(1 for n in self.new_rows if n.rule is None and n.kind != "transfer")

    @property
    def unknown_kind_count(self) -> int:
        """New rows nobody can classify yet (bank rows); Review decides what they are."""
        return sum(1 for n in self.new_rows if n.kind is None)

    @property
    def transfer_count(self) -> int:
        """New transfer legs (e.g. card payments). Matching them to the other account comes in phase 10."""
        return sum(1 for n in self.new_rows if n.kind == "transfer")


def normalize_description(text: str) -> str:
    return " ".join(text.upper().split())


def fingerprint(account_id: int, row: ParsedRow, occurrence: int) -> str:
    parts = [
        str(account_id),
        row.txn_date.isoformat() if row.txn_date else "",
        row.posted_date.isoformat() if row.posted_date else "",
        str(row.amount_cents),
        normalize_description(row.raw_description),
        str(occurrence),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def fingerprint_rows(account_id: int, rows: list[ParsedRow]) -> list[tuple[ParsedRow, str]]:
    """Fingerprint each row, numbering identical rows 1, 2, 3... within the file."""
    seen = Counter()
    result = []
    for row in rows:
        key = (row.txn_date, row.posted_date, row.amount_cents, normalize_description(row.raw_description))
        seen[key] += 1
        result.append((row, fingerprint(account_id, row, seen[key])))
    return result


def decode_csv(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252")  # some banks still export Windows-1252


def detect_account(conn: psycopg.Connection, data: bytes) -> int | None:
    """Guess which active account a file belongs to, or None if unsure.

    An account fits if its parser can read the file. The guess is used only
    when exactly one active account fits; with two accounts from the same
    bank, the user picks. (Masked card numbers inside descriptions are often
    per-merchant virtual numbers, so they are not used.)
    """
    text = decode_csv(data)
    readable: dict[str, bool] = {}
    candidates = []
    for account_id, parser_key in conn.execute(
        "SELECT id, parser_key FROM accounts WHERE active AND parser_key IS NOT NULL ORDER BY id"
    ):
        if parser_key not in readable:
            try:
                PARSERS[parser_key](text)
                readable[parser_key] = True
            except (KeyError, ValueError):
                readable[parser_key] = False
        if readable[parser_key]:
            candidates.append(account_id)
    return candidates[0] if len(candidates) == 1 else None


def end_of_day_balances(rows: list[ParsedRow]) -> list[tuple[date, int]]:
    """The balance at the end of each day, from rows that carry a running balance.

    Within one day the export's own order decides which row came last. Exports
    list rows either newest first or oldest first; the dates tell which. If
    every row is on the same day, newest first is assumed (BCU's order).
    Returned oldest first.
    """
    with_balance = [r for r in rows if r.balance_cents is not None]
    if not with_balance:
        return []
    days = [r.txn_date or r.posted_date for r in with_balance]
    oldest_first = days[0] < days[-1]
    balances: dict[date, int] = {}
    for day, row in zip(days, with_balance):
        if oldest_first or day not in balances:   # keep the day's last row in time
            balances[day] = row.balance_cents
    return sorted(balances.items())


def preview_import(conn: psycopg.Connection, account_id: int, filename: str, data: bytes) -> ImportPreview:
    account = conn.execute(
        "SELECT display_name, parser_key, owner_person_id, account_class FROM accounts WHERE id = %s", (account_id,)
    ).fetchone()
    if account is None:
        raise ValueError(f"No account with id {account_id}")
    account_name, parser_key, owner_id, account_class = account
    if parser_key is None:
        raise ValueError(f"{account_name} has no parser; its transactions are entered by hand")

    parsed = get_parser(parser_key)(decode_csv(data))
    if account_class == "liability":
        # On a credit card, anything that isn't a payment or transfer is a purchase, refund or fee.
        parsed = [replace(r, kind="expense") if r.kind is None else r for r in parsed]
    kept = [r for r in parsed if r.kind != "excluded"]
    excluded = [r for r in parsed if r.kind == "excluded"]

    rules = load_rules(conn)
    candidates = []
    for row, fp in fingerprint_rows(account_id, kept):
        rule = first_match(rules, row.raw_description, account_id, row.amount_cents,
                           row.kind, "parser" if row.kind else None)
        merchant = rule.merchant_name if rule and rule.merchant_name else clean_merchant(row.raw_description)
        candidates.append(NewRow(parsed=row, fingerprint=fp, merchant=merchant, rule=rule))
    existing = {
        r[0]
        for r in conn.execute(
            "SELECT fingerprint FROM transactions WHERE fingerprint = ANY(%s)",
            ([c.fingerprint for c in candidates],),
        )
    }
    sha = hashlib.sha256(data).hexdigest()
    earlier = [
        r[0]
        for r in conn.execute(
            "SELECT id FROM import_batches WHERE file_sha256 = %s AND reverted_at IS NULL ORDER BY id", (sha,)
        )
    ]

    return ImportPreview(
        account_id=account_id,
        account_name=account_name,
        purchaser_id=owner_id,
        filename=Path(filename).name,
        file_bytes=data,
        file_sha256=sha,
        rows_in_file=len(parsed),
        new_rows=[c for c in candidates if c.fingerprint not in existing],
        duplicate_rows=[c for c in candidates if c.fingerprint in existing],
        excluded_rows=excluded,
        already_imported_batch_ids=earlier,
        # Only asset accounts for now: how a card export states what is owed hasn't been seen yet.
        balances=end_of_day_balances(kept) if account_class == "asset" else [],
    )


def commit_import(conn: psycopg.Connection, preview: ImportPreview, archive: Path | None = None) -> int:
    """Write the batch and its new rows, archive the original file. Returns the batch id.

    Everything happens in one database transaction; if archiving the file
    fails, nothing is written.
    """
    archive = archive or archive_dir()
    with conn.transaction():
        batch_id = conn.execute(
            """
            INSERT INTO import_batches
                (account_id, filename, file_sha256, rows_in_file, rows_inserted, rows_duplicate, rows_excluded)
            VALUES (%s, %s, %s, %s, 0, 0, %s)
            RETURNING id
            """,
            (preview.account_id, preview.filename, preview.file_sha256, preview.rows_in_file, len(preview.excluded_rows)),
        ).fetchone()[0]

        inserted = 0
        for new in preview.new_rows:
            row = new.parsed
            cur = conn.execute(
                """
                INSERT INTO transactions
                    (source, account_id, txn_date, posted_date, amount_cents, raw_description, merchant,
                     kind, kind_source, category_id, category_source, rule_id,
                     purchaser_id, fingerprint, import_batch_id, raw_row)
                VALUES ('import', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (fingerprint) DO NOTHING
                """,
                (preview.account_id, row.txn_date, row.posted_date, row.amount_cents, row.raw_description,
                 new.merchant, new.kind, new.kind_source,
                 new.rule.category_id if new.rule else None,
                 "rule" if new.rule and new.rule.category_id is not None else None,
                 new.rule.id if new.rule else None,
                 preview.purchaser_id, new.fingerprint, batch_id, json.dumps(row.raw_row)),
            )
            inserted += cur.rowcount

        # Balances as the bank states them. A later import of the same day replaces the
        # earlier one (an export taken mid-day may not have had the whole day yet).
        for as_of, balance in preview.balances or []:
            conn.execute(
                """
                INSERT INTO balance_snapshots (account_id, as_of_date, balance_cents, source, import_batch_id)
                VALUES (%s, %s, %s, 'import', %s)
                ON CONFLICT (account_id, as_of_date, source)
                DO UPDATE SET balance_cents = EXCLUDED.balance_cents, import_batch_id = EXCLUDED.import_batch_id
                """,
                (preview.account_id, as_of, balance, batch_id),
            )

        # Rows that became duplicates since the preview (rare) count as duplicates.
        duplicates = len(preview.duplicate_rows) + (len(preview.new_rows) - inserted)
        conn.execute(
            "UPDATE import_batches SET rows_inserted = %s, rows_duplicate = %s WHERE id = %s",
            (inserted, duplicates, batch_id),
        )

        archive.mkdir(parents=True, exist_ok=True)
        (archive / f"{batch_id:06d}_{preview.filename}").write_bytes(preview.file_bytes)

    return batch_id


def undo_import(conn: psycopg.Connection, batch_id: int) -> int:
    """Delete the rows a batch inserted (and the balances it recorded) and mark it reverted.
    Returns rows deleted.

    The archived file is kept, so the batch can be re-imported later.
    """
    with conn.transaction():
        batch = conn.execute(
            "SELECT reverted_at FROM import_batches WHERE id = %s FOR UPDATE", (batch_id,)
        ).fetchone()
        if batch is None:
            raise ValueError(f"No import batch {batch_id}")
        if batch[0] is not None:
            raise ValueError(f"Batch {batch_id} was already undone at {batch[0]}")
        deleted = conn.execute("DELETE FROM transactions WHERE import_batch_id = %s", (batch_id,)).rowcount
        conn.execute("DELETE FROM balance_snapshots WHERE import_batch_id = %s", (batch_id,))
        conn.execute("UPDATE import_batches SET reverted_at = now() WHERE id = %s", (batch_id,))
    return deleted

