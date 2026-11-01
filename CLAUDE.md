# CLAUDE.md

Household spending tracker: Python + Streamlit app with a PostgreSQL backend on a Raspberry Pi.

## Before making changes
- Read `SPEC.md`. It is the source of truth for requirements and design decisions.
- If a task conflicts with SPEC.md or touches an item marked OPEN, ask before deciding.
- Work in the build phases listed in SPEC.md section 10, one phase at a time, and stop for review after each.

## Non-negotiables
- Money is stored as integer cents. Never use floats for amounts.
- Running totals and budget-to-date are computed, never stored.
- Rules must never overwrite a category where `category_source = 'manual'`.
- Keep raw bank data (`raw_description`, `raw_row`, archived CSVs).
- Schema changes go through versioned migrations.
- Never commit credentials, real CSV exports, or database dumps. Test fixtures use fake amounts and merchants.

## Environment
- Database host / name / user: TODO (fill in once set up; real credentials go in an untracked secrets file).
- Pi model and OS: TODO.
- Dev laptop: Windows (PowerShell). Give Windows and Mac/Linux variants for any commands in docs.

## Conventions
- Each card issuer's parser lives in its own module with a shared interface and its own test.
- Prefer clear, readable code over clever code; the owner should be able to follow it.
