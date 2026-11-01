# Household Spending Tracker: Requirements Spec

Status: requirements agreed, not yet built. Items marked **OPEN** still need a decision.

## 1. Purpose and scope

Give two partners shared visibility into household spending: where money goes, by category, against a monthly budget.

- The goal is **visibility, not scorekeeping.** Most spending is never attributed as a debt between the partners.
- Credit cards are ~95% of spending and come in as CSV exports. Cash and other non-card spending is entered by hand.
- An optional "who owes whom" ledger for specific items (mostly cash things expected to even out) is a **later phase**. The schema should leave room for it, but it is not built in v1.

Out of scope for v1: bank account / Venmo / Zelle imports, automatic bank connections, investment or income tracking, mobile app.

## 2. Design principles

1. **One transactions table; everything else is derived.** Running monthly totals and budget-to-date are calculated in queries/views, never stored. Category "pages" are filtered views of the same table.
2. **Bank-agnostic core.** Each card issuer gets a small parser that converts its CSV into one standard record. Nothing downstream of the parsers knows which bank a row came from. Manual entries produce the same record.
3. **Keep the raw data.** Store the bank's original description and the original CSV row, and archive the original files, so data can be reprocessed if a parser or category scheme changes.
4. **Importing and categorizing are separate steps.** Importing records facts. Categorizing is a judgment layered on top and refined over time.
5. **Manual choices are never overwritten by rules.**
6. **No forced receipt splitting.** Splits are optional; an unsplit transaction uses its single category.
7. **Minimal command line.** Day-to-day use happens entirely in a browser.

## 3. Architecture

- **Database:** PostgreSQL on a Raspberry Pi (always on).
- **App:** Python + Streamlit, also running on the Pi as a background service that starts on boot. Accessed from any browser on the home network.
- **Development:** code written on a laptop, pointed at the Pi's database (or a dev database on the Pi), then deployed to the Pi.
- **Money:** stored as integer cents (`BIGINT`). Positive = spending, negative = refund/credit.
- **Dates:** stored as `DATE`. No times or time zones needed.

## 4. Data model

Names are suggestions; Claude Code may refine them but should keep the structure.

### people
`id`, `name`. Two rows initially.

### cards
`id`, `display_name`, `issuer`, `last4`, `owner_person_id` (default purchaser for this card), `parser_key` (which parser handles its CSV), `active`.

### categories
Two-level tree in one table: `id`, `name`, `parent_id` (NULL = top-level), `sort_order`, `active`.

- Every transaction points at a **subcategory**; its top-level category is derived through `parent_id`.
- Each top-level category has a "General" subcategory so nothing is forced into a specific bucket.

Initial taxonomy (**OPEN**: confirm with both partners):

| Top-level | Subcategories |
|---|---|
| Transportation | Fuel, Tolls & parking, Maintenance & parts, General |
| Groceries & Household | General (default for any supermarket / Costco / Walmart run), Groceries, Household goods, Personal care |
| Dining & Going Out | Restaurants, Fast food, Coffee, Bars & entertainment, General |
| Recurring | Subscriptions, Bills, General |
| Discretionary | General, plus any the partners want |

Notes: "Gas" in the old spreadsheet becomes Transportation. "Other" splits into Recurring (predictable, committed) and Discretionary (behavior-driven), because they behave differently in a budget.

### import_batches
`id`, `card_id`, `filename`, `file_sha256`, `imported_at`, `rows_in_file`, `rows_inserted`, `rows_duplicate`, `rows_excluded`, `reverted_at` (NULL unless undone).

Original CSV files are copied to an archive directory on the Pi, named by batch id.

### transactions
| Column | Notes |
|---|---|
| `id` | |
| `source` | `card` or `manual` |
| `card_id` | NULL for manual entries |
| `txn_date` | NULL if the CSV doesn't provide it |
| `posted_date` | NULL for manual entries |
| `effective_date` | generated: `COALESCE(txn_date, posted_date)`. Used for all month/budget attribution |
| `amount_cents` | BIGINT, positive = spend, negative = refund |
| `raw_description` | exactly as the bank wrote it |
| `merchant` | cleaned display name |
| `category_id` | subcategory; NULL = uncategorized (goes to review queue) |
| `category_source` | `rule`, `manual`, or NULL |
| `rule_id` | which rule assigned it, if any |
| `purchaser_id` | defaults to card owner; editable |
| `note` | free text (e.g., what an Amazon order was) |
| `fingerprint` | UNIQUE; see dedup below |
| `import_batch_id` | NULL for manual entries |
| `raw_row` | JSONB copy of the original CSV row |
| `created_at`, `updated_at` | |

### splits (optional per transaction)
`id`, `transaction_id`, `amount_cents`, `category_id`, `note`.

- When a transaction has splits, reports use the split lines instead of the parent's category.
- Split amounts must sum exactly to the parent amount (enforce in the app and, ideally, with a database check).

### rules
`id`, `priority`, `match_type` (`contains`, `exact`, `regex`), `pattern` (matched against `raw_description`, case-insensitive), optional `card_id`, optional amount range, `category_id`, optional `merchant_name` (cleaned name to apply), `active`, `created_from_transaction_id`.

### budgets
`id`, `category_id` (top-level), `monthly_amount_cents`, `effective_from` (first month it applies). The budget for a month is the row with the latest `effective_from` on or before that month, so changing a budget never rewrites history.

**OPEN:** budget amounts, especially Dining & Going Out (the old "Food" budget covered both groceries and dining).

### Later phase: IOU ledger (not in v1)
- `iou_entries`: `id`, optional `transaction_id`, `owed_by`, `owed_to`, `amount_cents`, `date`, `note`.
- `settlements`: `id`, `from_person`, `to_person`, `amount_cents`, `date`, `note`.
- IOUs and settlements are transfers between partners. They **never** affect spending totals or budgets. Nothing is tracked unless someone deliberately flags it.

## 5. Import pipeline

1. User uploads one or more CSVs on the Import page and picks (or the app detects) which card each belongs to.
2. The card's parser converts each row to the standard record and classifies it as: purchase, refund, or excluded (card payment, balance transfer, rewards redemption, interest/fee adjustments, pending/unposted rows). Each parser handles its own bank's sign convention, date format, and column names.
3. **Deduplication:** fingerprint = hash of `card_id`, dates, `amount_cents`, normalized `raw_description`, and an occurrence index. The occurrence index distinguishes legitimate identical charges (two same-price coffees on one day): the nth identical row in a file gets index n. Insert with `ON CONFLICT (fingerprint) DO NOTHING`, so overlapping imports are harmless.
4. Merchant cleanup: generic cleanup (strip processor prefixes like `SQ *`, `TST*`, trailing store numbers) then rule-provided names.
5. Categorization rules run on new rows (see section 6).
6. Preview screen shows counts (new / duplicate / excluded / uncategorized) and the new rows before the user confirms. Nothing is written until confirmation.
7. A batch can be undone from the app, deleting the rows it inserted.

Parsers live in one module per issuer with a common interface, plus a test for each using a sample CSV (with fake amounts/merchants).

**OPEN:** which card issuers are used, with a header row and a few sample rows from each export.

## 6. Categorization

- Rules are applied in priority order; first match wins.
- Rules only set categories on rows where `category_source` is NULL or `rule`. Rows with `category_source = manual` are never touched.
- Re-running rules over existing data is supported (after rule edits).
- Unmatched rows go to the review queue. When the user categorizes one by hand, the app offers "save as rule" with a pre-filled pattern based on the cleaned merchant.

## 7. Reporting

Implement as Postgres views where practical:

- **Spending lines view:** unsplit transactions plus split lines flattened into one shape (date, amount, subcategory, top-level category, merchant, card, purchaser, note). All reports read from this.
- **Monthly totals** by top-level category and subcategory, by `effective_date` month.
- **Budget pace:** prorated budget on a given day = monthly budget × (day of month ÷ days in month). Compare month-to-date spending against it.
- **Running monthly total** per category, resetting each month.

**OPEN:** annual subscriptions: count in the month charged (simpler, default) or spread across 12 months?
**OPEN:** handling lumpy expenses like car maintenance: separate budget line, or excluded from the monthly pace view?

## 8. User interface (Streamlit)

1. **Import:** drag-and-drop CSVs, preview, confirm; list of past batches with undo.
2. **Review:** uncategorized queue in an editable table with category dropdowns and a "save as rule" checkbox.
3. **Transactions:** full table with filters (month, category, card, purchaser), inline editing of category, purchaser, and note, a split editor, and a manual-entry form for cash. Filtering by one top-level category reproduces the old per-sheet layout, with columns: date, amount, running monthly total, merchant, card, budget-to-date, note, purchaser.
4. **Dashboard:** per-category spending vs. budget pace for the current month, month-over-month trend, drill-down from top-level to subcategory.
5. **Settings:** budgets, cards, people, categories, rules.

Access control: a simple shared password on the app, since anyone on the home network could otherwise reach it.

## 9. Deployment and operations

- Postgres listens only on localhost (the app runs on the same Pi). The app uses its own database user with access to this database only. Credentials live in a config/secrets file excluded from git.
- Streamlit runs as a systemd service that starts on boot.
- **Backups (required):** nightly `pg_dump` via a scheduled job, copied off the Pi (another machine or cloud storage), with a retention policy. Document and test the restore procedure.
- Recommended: boot the Pi from a USB SSD rather than an SD card.
- Optional: Tailscale for access away from home. Do not expose the app to the public internet.
- Schema changes are made through versioned migration scripts, not by hand.

## 10. Build phases

1. Database schema, migrations, seed data (people, cards, categories).
2. First parser, tested against a real (anonymized) CSV, plus import from the command line to prove the pipeline.
3. Streamlit Import page with preview, dedup, and undo.
4. Rules engine and Review page.
5. Transactions page with editing, splits, and manual entry.
6. Budgets and Dashboard.
7. Deployment on the Pi: service, password, backups.
8. Later: IOU ledger; optional migration of history from the old spreadsheet.

## 11. Open questions (summary)

- Card issuers and sample CSVs for each.
- Final category taxonomy (both partners).
- Budget amounts, especially dining.
- Annual subscriptions: month charged vs. spread across the year.
- How to treat lumpy expenses like car maintenance.
- Whether to import historical data from the existing spreadsheet.
- Pi model and OS version (affects deployment details).
- Whether access away from home is needed.
