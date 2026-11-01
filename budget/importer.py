"""Import pipeline: CSV file -> preview -> (confirm) -> transactions.

Nothing is written to the database until commit_import() is called with a
preview, so the UI (Phase 3) and the command line can both show the counts
first and ask for confirmation.

Deduplication (SPEC section 5, step 3): each row gets a fingerprint built
from card, dates, amount, normalized description and an occurrence index,
where the nth identical row within one file gets index n. Two same-price
coffees on one day are therefore both kept, while importing an overlapping
file again inserts nothing new.
"""

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import psycopg

from budget.config import archive_dir
from budget.merchant import clean_merchant
from budget.parsers import PARSERS, get_parser
from budget.parsers.base import ParsedRow


@dataclass
class NewRow:
    parsed: ParsedRow
    fingerprint: str
    merchant: str


@dataclass
class ImportPreview:
    card_id: int
    card_name: str
    purchaser_id: int
    filename: str
    file_bytes: bytes
    file_sha256: str
    rows_in_file: int
    new_rows: list[NewRow]
    duplicate_rows: list[NewRow]
    excluded_rows: list[ParsedRow]
    already_imported_batch_ids: list[int]   # earlier, non-undone batches of this exact file

    @property
    def uncategorized_count(self) -> int:
        # The rules engine arrives in Phase 4; until then every new row is uncategorized.
        return len(self.new_rows)


def normalize_description(text: str) -> str:
    return " ".join(text.upper().split())


def fingerprint(card_id: int, row: ParsedRow, occurrence: int) -> str:
    parts = [
        str(card_id),
        row.txn_date.isoformat() if row.txn_date else "",
        row.posted_date.isoformat() if row.posted_date else "",
        str(row.amount_cents),
        normalize_description(row.raw_description),
        str(occurrence),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def fingerprint_rows(card_id: int, rows: list[ParsedRow]) -> list[tuple[ParsedRow, str]]:
    """Fingerprint each row, numbering identical rows 1, 2, 3... within the file."""
    seen = Counter()
    result = []
    for row in rows:
        key = (row.txn_date, row.posted_date, row.amount_cents, normalize_description(row.raw_description))
        seen[key] += 1
        result.append((row, fingerprint(card_id, row, seen[key])))
    return result


def decode_csv(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252")  # some banks still export Windows-1252


def detect_card(conn: psycopg.Connection, data: bytes) -> int | None:
    """Guess which active card a file belongs to, or None if unsure.

    A card fits if its parser can read the file. The guess is used only when
    exactly one active card fits; with two cards from the same bank, the user
    picks. (Masked card numbers inside descriptions are often per-merchant
    virtual numbers, so they are not used.)
    """
    text = decode_csv(data)
    readable: dict[str, bool] = {}
    candidates = []
    for card_id, parser_key in conn.execute("SELECT id, parser_key FROM cards WHERE active ORDER BY id"):
        if parser_key not in readable:
            try:
                PARSERS[parser_key](text)
                readable[parser_key] = True
            except (KeyError, ValueError):
                readable[parser_key] = False
        if readable[parser_key]:
            candidates.append(card_id)
    return candidates[0] if len(candidates) == 1 else None


def preview_import(conn: psycopg.Connection, card_id: int, filename: str, data: bytes) -> ImportPreview:
    card = conn.execute(
        "SELECT display_name, parser_key, owner_person_id FROM cards WHERE id = %s", (card_id,)
    ).fetchone()
    if card is None:
        raise ValueError(f"No card with id {card_id}")
    card_name, parser_key, owner_id = card

    parsed = get_parser(parser_key)(decode_csv(data))
    kept = [r for r in parsed if r.kind != "excluded"]
    excluded = [r for r in parsed if r.kind == "excluded"]

    candidates = [
        NewRow(parsed=row, fingerprint=fp, merchant=clean_merchant(row.raw_description))
        for row, fp in fingerprint_rows(card_id, kept)
    ]
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
        card_id=card_id,
        card_name=card_name,
        purchaser_id=owner_id,
        filename=Path(filename).name,
        file_bytes=data,
        file_sha256=sha,
        rows_in_file=len(parsed),
        new_rows=[c for c in candidates if c.fingerprint not in existing],
        duplicate_rows=[c for c in candidates if c.fingerprint in existing],
        excluded_rows=excluded,
        already_imported_batch_ids=earlier,
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
                (card_id, filename, file_sha256, rows_in_file, rows_inserted, rows_duplicate, rows_excluded)
            VALUES (%s, %s, %s, %s, 0, 0, %s)
            RETURNING id
            """,
            (preview.card_id, preview.filename, preview.file_sha256, preview.rows_in_file, len(preview.excluded_rows)),
        ).fetchone()[0]

        inserted = 0
        for new in preview.new_rows:
            row = new.parsed
            cur = conn.execute(
                """
                INSERT INTO transactions
                    (source, card_id, txn_date, posted_date, amount_cents, raw_description, merchant,
                     purchaser_id, fingerprint, import_batch_id, raw_row)
                VALUES ('card', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (fingerprint) DO NOTHING
                """,
                (preview.card_id, row.txn_date, row.posted_date, row.amount_cents, row.raw_description,
                 new.merchant, preview.purchaser_id, new.fingerprint, batch_id, json.dumps(row.raw_row)),
            )
            inserted += cur.rowcount

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
    """Delete the rows a batch inserted and mark it reverted. Returns rows deleted.

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
        conn.execute("UPDATE import_batches SET reverted_at = now() WHERE id = %s", (batch_id,))
    return deleted

