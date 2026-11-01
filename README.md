# Household Spending Tracker

Python + Streamlit + PostgreSQL, running on a Raspberry Pi. See `SPEC.md` for requirements and `CLAUDE.md` for working rules.

**Status:** Phases 1–3 are done: schema and seed data, the Citi parser and import pipeline, and the Import page. Later phases are listed in SPEC.md section 10.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config/secrets.example.toml config/secrets.toml   # then edit: real DB credentials
cp seed/local.example.toml seed/local.toml           # then edit: real names and cards
```

Both copied files are gitignored. You can also set `BUDGET_DATABASE_URL` instead of using `secrets.toml`.

## Database

```bash
python -m budget.migrate            # apply pending migrations
python -m budget.migrate --status   # show applied / pending
python -m budget.seed               # add missing categories, people, cards (safe to re-run)
```

- **Migrations** are numbered SQL files in `migrations/`. Never edit one that has been applied; add a new file instead. The runner wraps each file in a transaction and records it in `schema_migrations`.
- **Seeding** only inserts rows that are missing (matched by name). It never renames or deletes, so edits made later in the app are safe.

## Running the app

```bash
streamlit run app/streamlit_app.py
```

Then open http://localhost:8501. The **Import** page takes CSV uploads (drag and drop, several at once). It picks the card when only one active card's parser can read the file, shows a preview (new / duplicate / excluded), and writes nothing until you click Import. Past imports are listed below it, with undo.

## Importing from the command line

```bash
python -m budget.import_csv --cards                    # find the card id
python -m budget.import_csv --card 1 export.csv        # preview, then confirm
python -m budget.import_csv --batches                  # past imports
python -m budget.import_csv --undo 7                   # undo batch 7
```

Each card's `parser_key` picks its parser (`citi` is the only one so far). Re-importing overlapping exports is safe: duplicates are skipped. Original files are archived to `archive/` (or `[paths] archive_dir` in `secrets.toml`).

## Tests

The tests need a Postgres server where they may create a database. They create a throwaway `budget_test_*` database and drop it at the end.

```bash
TEST_DATABASE_URL=postgresql://postgres@localhost:5432/postgres pytest -q
```

## Layout

```
app/           Streamlit app (streamlit_app.py + pages/)
budget/        Python package: config, db, migrate, seed, money, merchant, importer, import_csv
budget/parsers one module per card issuer (citi.py), sharing base.ParsedRow
migrations/    versioned schema changes
seed/          categories.toml (tracked), local.toml (untracked: people and cards)
config/        secrets.toml (untracked)
tests/         pytest suite; fixtures use fake data only
```
