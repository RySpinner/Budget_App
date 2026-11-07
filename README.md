# Household Spending Tracker

Python + Streamlit + PostgreSQL, running on a Raspberry Pi. See `SPEC.md` for requirements and `CLAUDE.md` for working rules.

**Status:** v1 (phases 1–7: credit-card spending, deployed on the Pi) is done. v2 is under way (SPEC.md): phases 8 (accounts foundation) and 9 (BCU checking, balance snapshots, transfer rules) are done; transfer matching and balance reports come next. **To install on the Pi, follow [docs/deploy.md](docs/deploy.md).** Later phases are listed in SPEC.md section 10.

## Setup

Create a virtual environment and install the dependencies. The commands differ by system.

**Windows (PowerShell)**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy config\secrets.example.toml config\secrets.toml   # then edit: real DB credentials
copy seed\local.example.toml seed\local.toml           # then edit: real names and accounts
```

If PowerShell refuses to run `Activate.ps1` ("running scripts is disabled"), run this once and then try again:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
In the older Command Prompt (cmd), activate with `.venv\Scripts\activate.bat` instead.

**Mac / Linux / Raspberry Pi**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config/secrets.example.toml config/secrets.toml   # then edit: real DB credentials
cp seed/local.example.toml seed/local.toml           # then edit: real names and accounts
```

Activate the environment again in each new terminal before running the commands below.

Both copied files are gitignored. You can also set `BUDGET_DATABASE_URL` instead of using `secrets.toml` (PowerShell: `$env:BUDGET_DATABASE_URL = "postgresql://..."`).

## Local PostgreSQL (development)

PostgreSQL **15 or newer** is required. Use 17 to match the Raspberry Pi.

**Windows**
1. Install from https://www.postgresql.org/download/windows/ (the EDB installer). Keep the defaults: server, pgAdmin, command-line tools, port 5432. Write down the `postgres` password you choose. Skip Stack Builder.
2. Open **SQL Shell (psql)** from the Start menu, press Enter to accept the defaults, then enter the `postgres` password and run:
   ```sql
   CREATE ROLE budget_app LOGIN PASSWORD 'pick-a-password';
   CREATE DATABASE household_budget OWNER budget_app;
   ```
3. Put that password in `config\secrets.toml`. Avoid `@ : / #` in passwords that go into `postgresql://` URLs.

**Mac / Linux:** install PostgreSQL with your package manager (or Postgres.app on a Mac), then run the same two SQL lines as the `postgres` user.

**Docker (any system):** `docker run -d --name budget-pg -e POSTGRES_PASSWORD=devpass -p 5432:5432 postgres:17`

To start the database over, connect as `postgres` to the `postgres` database and run `DROP DATABASE household_budget;` then the `CREATE DATABASE` line above. Then run migrate and seed again.

## Database

```bash
python -m budget.migrate            # apply pending migrations
python -m budget.migrate --status   # show applied / pending
python -m budget.seed               # add missing categories, people, accounts (safe to re-run)
```

- After pulling new code, run `python -m budget.migrate` again: it applies any new migrations (e.g. `0004_accounts.sql`) and skips ones already applied.
- **Migrations** are numbered SQL files in `migrations/`. Never edit one that has been applied; add a new file instead. The runner wraps each file in a transaction and records it in `schema_migrations`.
- **Seeding** only inserts rows that are missing (matched by name). It never renames or deletes, so edits made later in the app are safe. If `local.toml` disagrees with an existing account, it prints a warning; change that account in the database instead, e.g. `UPDATE accounts SET parser_key = 'citi' WHERE id = 2;`.
- **Adding a category:** add it to `seed/categories.toml` and run `python -m budget.seed`. To retire one, set `active = false` in the database rather than deleting it; if you rename one, rename it in the TOML file too.

## Running the app

```bash
streamlit run app/streamlit_app.py
```

The app asks for a shared password. Set one with `python -m budget.auth` and paste the `[app]` lines it prints into `config/secrets.toml`. On a development laptop you can turn the check off instead by adding this to `config/secrets.toml`:
```toml
[app]
require_password = false
```
Never use that on the Pi.

Then open http://localhost:8501. Pages:

- **Dashboard** (start page) shows the month's spending against budget pace for each category. Pace = budget × day of month ÷ days in month. It also shows the last 12 months by category and a drill-down from one category to its subcategories. Every chart has a table.
- **Budgets:** set each top-level category's monthly amount from a chosen month onward. Earlier months keep their old budget.
- The **Import** page takes CSV uploads (drag and drop, several at once). It picks the account when only one active account's parser can read the file, shows a preview (new / duplicate / excluded / uncategorized / transfers), and writes nothing until you click Import. Past imports are listed below it, with undo.

- **Review** lists uncategorized transactions. Pick a category for any row, or *Transfer between our accounts* (optionally naming the **Other account**); tick *Save as rule* to also create a rule (pre-filled pattern, editable) that handles matching rows from now on. A transfer rule saved here only applies to that row's account.
- **Transactions** shows spending by default; the **Kind** filter switches to income, transfers, rows whose kind isn't known yet, or everything. Further filters: month, category, subcategory, account and purchaser. Choosing one top-level category gives the old per-sheet layout: date, amount, running monthly total, merchant, account, budget-to-date, note, purchaser. Edit category (or choose *Transfer between our accounts*), note and purchaser in the table and press Save. Below the table you can split a spending or income row across categories (lines must add up exactly), delete a manual entry, and add a manual entry (the Cash account by default).
- **Rules** lists every rule with how many transactions it currently covers. A rule **sets** a category, or marks rows as a transfer between your accounts (with an optional **other account**, used later to pair the two sides). Edit, deactivate or delete rules, or add new ones, then re-run them. Rules are tried by priority (lower first) and the first match wins. Rules never change a category or kind you chose by hand.

Rules also run automatically on new rows during import; the preview shows which category each row will get. To re-run all rules from the command line: `python -m budget.rules`.

## Importing from the command line

```bash
python -m budget.import_csv --accounts                 # find the account id
python -m budget.import_csv --account 2 export.csv    # preview, then confirm
python -m budget.import_csv --batches                  # past imports
python -m budget.import_csv --undo 7                   # undo batch 7
```

Each account's `parser_key` picks its parser: `citi`, `chase`, `bcu` (BCU credit union: the Visa card and checking use the same format) or `amex` so far. For bank accounts, the running balance in the file is saved as the account's end-of-day balance, and rows the parser can't classify (spending, income or transfer?) wait in Review. When the Import page can tell from a file's format which single account it belongs to, it picks the account automatically. Card payments are kept as **transfers** (money moving between your own accounts), never counted as spending. Re-importing overlapping exports is safe: duplicates are skipped. Original files are archived to `archive/` (or `[paths] archive_dir` in `secrets.toml`).

## Tests

The tests need a Postgres server where they may create a database. They create a throwaway `budget_test_*` database and drop it at the end.

Windows (PowerShell):

```powershell
$env:TEST_DATABASE_URL = "postgresql://postgres@localhost:5432/postgres"
pytest -q
```

Mac / Linux:

```bash
TEST_DATABASE_URL=postgresql://postgres@localhost:5432/postgres pytest -q
```

Use a Postgres user that is allowed to create databases. With the default `postgres` user, add its password to the URL (`postgresql://postgres:PASSWORD@localhost:5432/postgres`).

## Project notes

- `DECISIONS.md`: choices made while building, with the ones to reassess listed under *Review*.
- `TODO.md`: reminders and open items.
- `docs/sessions/`: a short summary of each build session.

## Layout

```
app/           Streamlit app (streamlit_app.py, views/ = one file per page, charts.py, ui.py, auth_gate.py)
budget/        Python package: config, db, migrate, seed, money, merchant, importer, import_csv, rules, categories, transactions, budgets, reports
budget/parsers one module per institution (citi.py, chase.py, bcu.py, amex.py), sharing base.ParsedRow
migrations/    versioned schema changes
seed/          categories.toml (tracked), local.toml (untracked: people and accounts)
config/        secrets.toml (untracked)
deploy/        Pi files: systemd service + backup timer, backup / restore / restore-test scripts
docs/          deploy.md (Pi setup guide), sessions/ (build session notes)
tests/         pytest suite; fixtures use fake data only
```
