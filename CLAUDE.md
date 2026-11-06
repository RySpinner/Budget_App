# CLAUDE.md

Household finances tracker: Python + Streamlit app with a PostgreSQL backend on a Raspberry Pi.
v1 (credit-card spending) is built; v2 (bank accounts, transfers, balances: "double-entry lite") is specced in SPEC.md, phases 8–12.

## Before making changes
- Read `SPEC.md`. It is the source of truth for requirements and design decisions.
- If a task conflicts with SPEC.md or touches an item marked OPEN, ask before deciding.
- Work in the build phases listed in SPEC.md section 10, one phase at a time, and stop for review after each.
- Read `DECISIONS.md` and `TODO.md` too. When you make a call SPEC.md doesn't settle, add it to the
  *Review* list in `DECISIONS.md` (the owner reassesses these later). When the owner decides something,
  record it under *Decided* with the date. Add reminders and open items to `TODO.md`.
- At the end of a session, add a short summary in `docs/sessions/<date>.md`.

## Non-negotiables
- Money is stored as integer cents. Never use floats for amounts.
- Running totals and budget-to-date are computed, never stored.
- Rules must never overwrite a category where `category_source = 'manual'` (from v2, the same applies to `kind_source` and transfer links).
- Transfers never count as spending or income: the two legs of a transfer sum to zero and stay out of spending reports.
- Keep raw bank data (`raw_description`, `raw_row`, archived CSVs).
- Schema changes go through versioned migrations.
- Never commit credentials, real CSV exports, or database dumps. Test fixtures use fake amounts and merchants.

## Environment
- Database (Pi): host `localhost`, database `household_budget`, user `budget_app`. Real credentials live only in the untracked `config/secrets.toml` and `~budget/.pgpass`.
- Pi: Raspberry Pi 5, Raspberry Pi OS 64-bit. Setup steps in `docs/deploy.md`; app in `/opt/household-budget` as the `budget` system user.
- Backups: nightly `deploy/backup.sh`, pushed with rsync over SSH to the owner's Mac / Linux machine / NAS.
- Dev laptop: Windows (PowerShell). Give Windows and Mac/Linux variants for any commands in docs.

## Conventions
- Each card issuer's parser lives in its own module with a shared interface and its own test.
- Page files live in `app/views/`, NOT `app/pages/`: Streamlit auto-lists a `pages/` folder, which would bypass the login gate.
- Prefer clear, readable code over clever code; the owner should be able to follow it.
