"""Keep seed/local.toml in step between machines that share one database.

`push` saves your local.toml into the database; `pull` downloads that copy to
this machine. Only local.toml is handled. config/secrets.toml is never stored
in the database (it holds the credentials needed to reach it).

Usage:
    python -m budget.local_sync status
    python -m budget.local_sync push
    python -m budget.local_sync pull            # refuses to replace a different local file
    python -m budget.local_sync pull --force    # replaces it, keeping the old one as local.toml.bak
    python -m budget.local_sync push --file path/to/other.toml
"""

import argparse
import hashlib
import os
import shutil
import tomllib
from datetime import datetime
from pathlib import Path

import psycopg

from budget.db import connect
from budget.seed import LOCAL_FILE

SHARED_NAME = "local.toml"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_file(path: Path) -> str:
    # newline="" keeps line endings exactly as they are (Windows vs Mac/Linux).
    with path.open(encoding="utf-8", newline="") as f:
        return f.read()


def get_stored(conn: psycopg.Connection, name: str = SHARED_NAME) -> tuple[str, datetime] | None:
    """The database's copy as (content, updated_at), or None if there isn't one."""
    row = conn.execute("SELECT content, updated_at FROM shared_files WHERE name = %s", (name,)).fetchone()
    return (row[0], row[1]) if row else None


def push(conn: psycopg.Connection, path: Path, name: str = SHARED_NAME) -> str:
    """Save the file into the database, replacing the earlier copy. Returns what happened.

    The file must be valid TOML, so a half-edited file can't replace a good copy.
    """
    if not path.exists():
        raise FileNotFoundError(f"{path} not found.")
    text = _read_file(path)
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{path} is not valid TOML, so it was not saved: {e}") from e

    stored = get_stored(conn, name)
    if stored and stored[0] == text:
        return "unchanged"
    conn.execute(
        """
        INSERT INTO shared_files (name, content, sha256, updated_at) VALUES (%s, %s, %s, now())
        ON CONFLICT (name) DO UPDATE SET content = EXCLUDED.content, sha256 = EXCLUDED.sha256,
                                         updated_at = now()
        """,
        (name, text, _sha256(text)),
    )
    return "replaced" if stored else "saved"  # the caller commits (main() does, on leaving `with connect()`)


def pull(conn: psycopg.Connection, path: Path, force: bool = False, name: str = SHARED_NAME) -> str:
    """Write the database's copy to `path`. Returns what happened.

    A local file that differs from the database copy is left alone unless
    force is set; then it is kept as `<name>.bak` first.
    """
    stored = get_stored(conn, name)
    if stored is None:
        raise LookupError(f"The database has no {name} yet. Run `python -m budget.local_sync push` on the machine that has it.")
    content = stored[0]

    result = "downloaded"
    if path.exists():
        if _read_file(path) == content:
            return "unchanged"
        if not force:
            raise FileExistsError(
                f"{path} differs from the database copy. Nothing was changed. "
                "Run `push` if your local file is the newer one, or `pull --force` to replace it "
                "(the old file is kept as .bak)."
            )
        shutil.copy2(path, path.with_name(path.name + ".bak"))
        result = "replaced"

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(content)
    if os.name == "posix":
        path.chmod(0o600)  # personal details: owner-only, like secrets.toml
    return result


def status(conn: psycopg.Connection, path: Path, name: str = SHARED_NAME) -> str:
    """One line comparing the local file with the database copy."""
    stored = get_stored(conn, name)
    local = _read_file(path) if path.exists() else None
    if stored is None:
        return "The database has no copy yet." + (" Run `push` to save yours." if local is not None else "")
    when = stored[1].strftime("%Y-%m-%d %H:%M")
    if local is None:
        return f"No local file. The database copy was saved {when}; run `pull` to download it."
    if local == stored[0]:
        return f"In step: your file matches the database copy (saved {when})."
    return f"Different: your file and the database copy (saved {when}) don't match. `push` to save yours, or `pull --force` to take the database's."


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["push", "pull", "status"])
    parser.add_argument("--file", type=Path, default=LOCAL_FILE, help="default: seed/local.toml")
    parser.add_argument("--force", action="store_true", help="pull: replace a different local file (keeps a .bak)")
    args = parser.parse_args()

    with connect() as conn:
        try:
            if args.action == "push":
                print(f"{args.file}: {push(conn, args.file)}.")
            elif args.action == "pull":
                print(f"{args.file}: {pull(conn, args.file, force=args.force)}.")
            else:
                print(status(conn, args.file))
        except (FileNotFoundError, FileExistsError, LookupError, ValueError) as e:
            raise SystemExit(f"Error: {e}") from None


if __name__ == "__main__":
    main()
