---
name: new-parser
description: Add a CSV parser for a new card or bank account institution (or a new account type at an existing one) from a sample export the owner provides. Use when the owner shares a bank/card CSV, says "here's <bank>", or runs /new-parser.
argument-hint: "<path to sample CSV> [institution name / parser key]"
---

# /new-parser

Build a parser for one institution's CSV export, following the pattern of
`budget/parsers/citi.py`, `chase.py`, `bcu.py` and `amex.py`. Read
CLAUDE.md, SPEC.md §5 and the DECISIONS.md entries about parsers first if
you haven't this session.

Arguments: `$ARGUMENTS` (a sample CSV path, usually under the uploads folder,
plus optionally the institution name or parser key).

## Ground rules (from CLAUDE.md)
- **The owner's file is real bank data. It never goes into the repo.** Read it,
  run it through the parser, report what came out, and leave it where it is.
  Fixtures are written by hand with fake merchants, fake amounts and
  all-zero/9 account and reference numbers.
- Money is integer cents via `budget.money.parse_cents`; never floats.
- Keep raw data: every field goes into `raw_row` by column name.
- One module per institution: `budget/parsers/<key>.py`, its own test
  `tests/test_parser_<key>.py`, fixtures in `tests/fixtures/<key>/`.
- Anything the sample can't settle is a guess: record it in DECISIONS.md
  (Review list) and ask for the missing rows in TODO.md. If it touches an
  OPEN item (e.g. rewards as income vs negative expense), keep the current
  behaviour and ask.

## 1. Read the sample exactly as downloaded
Read the raw text (not via Excel). Note:
- **Header**: exact column names, trailing comma, BOM, line endings (CRLF?).
- **Dates**: format (`%m/%d/%Y`, `%m/%d/%y`...), one date or transaction + posted.
- **Sign convention**: is a purchase / withdrawal positive or negative?
  Ours: **positive = money out / against the household** (purchase, withdrawal,
  more owed); negative = refund, deposit, payment received.
- **Amount text**: `$`, thousands commas, parentheses, whole dollars (`100`),
  separate Debit/Credit columns. **Are amounts quoted?** If not, a thousands
  comma splits the field (Citi: read the row from both ends).
- **Descriptions**: quoted? Can a comma split them (re-join the middle, as
  chase.py / bcu.py do)? Line breaks inside quoted fields (amex.py)?
- **Row type hints**: Type / Status / Transaction ID suffix columns; the
  wording of payments ("PAYMENT - THANK YOU", "AUTOPAY", "Pmt to card"),
  direct deposits ("DIR DEP"), rewards, pending.
- **Balance column** (bank accounts): running balance after each row?
- Blank rows, footer lines, multiple accounts in one file.

If the institution or account type isn't clear, ask the owner (one short
question) for the parser key. Use a short lowercase key (`bcu`, `amex`).
Check `budget/parsers/__init__.py` first: if the format matches an existing
parser (BCU uses one format for card and checking), extend that parser
instead of writing a new one.

## 2. Decide each row's kind
Return `ParsedRow(kind=...)` from `budget/parsers/base.py`:

| Row | kind | reason |
|---|---|---|
| Card payment received (card side) | `"transfer"` | `"card payment"` |
| Payment to a card (bank side, e.g. "Pmt to card") | `"transfer"` | `"card payment"` |
| Balance transfer between cards | `"transfer"` | `"balance transfer"` |
| Direct deposit / payroll | `"income"` | `"direct deposit"` |
| Rewards / cash back / points redemption | `rewards_row(amount)` from base.py: `None` + `"rewards credit"` (stored, waits in Review), or `"excluded"` + `"points only"` for a $0 row | decided 2026-11-14 |
| Pending / not posted | `"excluded"` | `"pending"` |
| Card benefit statement credit (e.g. streaming credit) | expense (a refund) | decided 2026-11-06 |
| Interest and fees | expense | decided 2026-11-01 |
| Anything else | `"expense"` if the file says it's a purchase/refund (a Type column), else `None` | |

Anything that posts in real currency is stored; only pending rows,
non-transaction lines and points-only rows are `"excluded"`.

`None` means unknown: on a credit card the importer turns it into
`"expense"` (except a rewards credit, which stays unknown); in a bank
account it goes to Review. The `reason` is stored as `transactions.parser_hint`. So a format shared by a
card and a bank account should return `None` for ordinary rows (see bcu.py).
Use the institution's own Type/Status column when it exists rather than
guessing from the description (chase.py).

Bank accounts: set `balance_cents` from a running-balance column, as the
bank states it (money held positive). The importer saves end-of-day
snapshots for asset accounts.

## 3. Write the parser
Copy the shape of the closest existing parser. Every parser has:
- A module docstring that documents the format: the header, one example row
  (faked), the sign convention, how kinds are decided, and what is still
  unconfirmed.
- `EXPECTED_COLUMNS` and an exact header check (tolerating a trailing comma)
  that raises `ValueError("This doesn't look like a <Name> export. ...")`.
  The Import page detects the account by which parser accepts the file, so
  the check must reject every other institution's format.
- `parse(text) -> list[ParsedRow]`: skip all-blank rows, wrap row errors
  as `ValueError(f"Line {n}: ...")`.
- `_parse_row(fields)`: dates, `amount_cents` in our sign, `raw_description`
  (whitespace collapsed if it can contain line breaks), kind/reason,
  `raw_row` = dict by column name (`_extra` for surplus fields), and
  `balance_cents` if there's a balance.
- `SPECIAL_ROWS` list of `(regex, kind, reason)` for description-based kinds.

Register it in `budget/parsers/__init__.py` (`PARSERS` dict and the import).

## 4. Fixture and tests
`tests/fixtures/<key>/sample.csv` (and e.g. `checking.csv` for a second
account type), fake data in the institution's exact format. Cover what
applies:
- a purchase; two identical rows on one day (both must be kept);
- a refund; a card payment; interest or a fee; a rewards row; a pending row;
- a description with a comma; an amount of $1,000 or more;
- line breaks inside fields, blank trailing rows, CRLF;
- bank: a direct deposit, a card payment, an unknown row, two rows on one
  day with balances (end-of-day check).

`tests/test_parser_<key>.py`, mirroring `test_parser_amex.py`:
- registered under its key;
- one test per row type (kind, reason, amount in our sign, dates);
- `raw_row` keeps the original text;
- a bad date reports `Line 2`.

`tests/test_parser_formats.py` checks automatically that the new parser
accepts only its own fixtures and that every other parser rejects them.
Don't add cross-format tests by hand.

## 5. Check against the real sample
Run the owner's file through the parser without copying it:

```bash
python -I -c "
import sys; sys.path.insert(0, '.')
from budget.parsers import get_parser
from budget.parsers.base import describe
from budget.merchant import clean_merchant
for r in get_parser('<key>')(open(sys.argv[1], encoding='utf-8-sig').read()):
    print(r.txn_date, r.amount_cents, describe(r.kind, r.amount_cents, r.reason), '|', clean_merchant(r.raw_description), '|', r.balance_cents)
" "<path to sample>"
```

If merchant names come out ugly in a way that will recur (reference
numbers, store numbers glued to cities, bank prefixes), improve
`budget/merchant.py` and add a case to `tests/test_merchant.py`. Otherwise
leave it to rules.

Optionally import the fake fixture end to end into a throwaway database
(migrate, seed a `local.toml` in the scratchpad, `python -m budget.import_csv
--account N --yes`, re-import to see all duplicates), then drop it.

## 6. Run the tests
Run the whole suite with `TEST_DATABASE_URL` pointing at a Postgres where
you can create databases (see `tests/conftest.py`); each run uses its own
throwaway database. Without it the database tests are skipped, not passed.

## 7. Update the docs
- `DECISIONS.md` Review list: the next numbers, one per call you made
  (parser key, how payments/rewards/pending are recognised, what's assumed).
- `TODO.md`: an owner item asking for the rows you haven't seen; update
  "Done so far" under *Other card issuers*.
- `README.md`: the parser keys sentence and the `budget/parsers` line.
- `SPEC.md` §5 "Institutions so far".
- `seed/local.example.toml`: the "Available" list.
- `docs/sessions/<today>.md`: a short entry.

## 8. Report
Tell the owner, in plain terms:
- how each row of their sample was classified (a small table);
- how to use it: `parser_key = "<key>"` in `seed/local.toml`, then
  `python -m budget.seed`;
- what is still a guess, and which rows to send next.
Commit and push if that's how this session has been working.
