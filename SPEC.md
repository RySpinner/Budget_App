# Household Finances Tracker: Requirements Spec

Status:
- **v1 (phases 1–7)** is built: credit-card spending tracker, deployed on a Pi.
- **v2 (phases 8–12)** extends it to checking and savings accounts with light double-entry bookkeeping. Agreed 2026-11-06; not yet built.

Items marked **OPEN** still need a decision. Items marked **PINNED** are in scope, but their detailed design is deferred because they need extra review or care.

## 1. Purpose and scope

Give two partners shared visibility into household finances:
- where money goes, by category, against a monthly budget (v1);
- where it comes from (income), how it moves between accounts, and what each account holds (v2).

Principles of scope:
- The goal is **visibility, not scorekeeping.** Most spending is never attributed as a debt between the partners.
- **Accounts** cover credit cards, checking, savings and a cash "wallet". The model must also be able to hold investment accounts later (section 4, accounts).
- Accounts come in as CSV exports. Anything without an export (cash spending, odd items) is entered by hand.
- **Bookkeeping is "double-entry lite"** (section 2, principle 8). It's thorough enough that balances reconcile and nothing is counted twice, but simple enough for a household. No journals, no debits and credits in the UI.
- An optional "who owes whom" ledger (IOUs) is a **later phase** (phase 13). The schema leaves room for it.

In scope but **PINNED** (needs its own design pass and more review than ordinary rows): ATM withdrawals and cash, Venmo / Zelle / peer-to-peer payments, paper checks. See section 5a.

Out of scope for now: automatic bank connections (Plaid etc.), investment *holdings* and trades (investment *accounts* are a future feature, section 4), mobile app, gross pay and tax details (income is what lands in the account).

## 2. Design principles

1. **One transactions table; everything else is derived.** Running totals, budget-to-date, account balances, net worth and cash flow are calculated in queries/views, never stored. Category "pages" are filtered views of the same table.
2. **Institution-agnostic core.** Each institution gets a small parser that converts its CSV into one standard record. Nothing downstream of the parsers knows which bank a row came from. Manual entries produce the same record.
3. **Keep the raw data.** Store the bank's original description and the original CSV row, and archive the original files, so data can be reprocessed if a parser or category scheme changes.
4. **Importing and categorizing are separate steps.** Importing records facts. Categorizing (and matching transfers) is a judgment layered on top and refined over time.
5. **Manual choices are never overwritten by rules.** This applies to categories and to a transaction's kind and transfer link.
6. **No forced receipt splitting.** Splits are optional; an unsplit transaction uses its single category.
7. **Minimal command line.** Day-to-day use happens entirely in a browser.
8. **Double-entry lite:**
   - Every transaction belongs to exactly one account.
   - Its *other side* is either a **category** (an expense or income) or **another of the household's accounts** (a transfer). Until that's known, it waits in Review.
   - The two halves ("legs") of a transfer are linked and **sum to zero**. Paying a card from checking is a transfer, which is why it is never counted as spending twice.
   - Account balances are computed from an opening balance plus transactions, and **reconciled** against balances the bank reports.
9. **Not overwhelming.** Unambiguous transfers are matched automatically. People only see the rows that genuinely need a decision.

## 3. Architecture

- **Database:** PostgreSQL on a Raspberry Pi (always on).
- **App:** Python + Streamlit, also running on the Pi as a background service that starts on boot. Accessed from any browser on the home network.
- **Development:** code written on a laptop, pointed at the Pi's database (or a dev database), then deployed to the Pi.
- **Money:** stored as integer cents (`BIGINT`).
  - **Sign convention** (unchanged from v1): a positive amount works *against* the household. It's money leaving an asset account (checking, savings, cash), or more owed on a liability (credit card). Negative is the reverse: a deposit, a refund, a card payment received.
  - So for an asset, balance = opening − Σ amounts; for a liability, amount owed = opening + Σ amounts.
- **Dates:** stored as `DATE`. No times or time zones needed.

## 4. Data model

Names are suggestions; Claude Code may refine them but should keep the structure. v2 changes are marked *(v2)*.

### people
`id`, `name`. Rows: the two partners, plus **"Joint"** *(v2)*, used as the owner of joint accounts and the purchaser of joint purchases. IOUs (phase 13) are only between real people, never Joint.

### accounts *(v2, replaces v1 `cards`)*
`id`, `display_name`, `institution`, `account_class`, `account_type`, `last4`, `owner_person_id` (default purchaser; may be Joint), `parser_key` (which parser reads its CSV; NULL for accounts only entered by hand, like Cash), `opening_balance_cents`, `opening_balance_date`, `active`.

- `account_class`: `asset` or `liability`. It decides how balances add up (section 3).
- `account_type` (a subtype within the class):
  - asset: `checking`, `savings`, `cash`
  - liability: `credit_card`
  - Reserved for the future: `brokerage`, `retirement` (asset); `loan` (liability).
- One built-in **Cash** account (asset, type `cash`, no parser) holds hand-entered cash spending and receives ATM withdrawals (PINNED, section 5a).
- *Future investment accounts:* tracked by value only, through balance snapshots (below), with no holdings or trades. Contributions are transfers from checking.

### balance_snapshots *(v2)*
`id`, `account_id`, `as_of_date`, `balance_cents` (as the bank states it, in the account's own terms: money held for assets, money owed for liabilities), `source` (`import` from a CSV balance column, `statement`, or `manual`).

These are facts from the bank, never computed. Reconciliation compares them with computed balances.

### categories
Two-level tree in one table: `id`, `name`, `parent_id` (NULL = top-level), `kind` *(v2: `expense` or `income`, set on the top level and inherited by its subcategories)*, `sort_order`, `active`.

- Every expense/income transaction points at a **subcategory**; its top-level category is derived through `parent_id`.
- Each top-level category has a "General" subcategory so nothing is forced into a specific bucket.
- Budgets apply to **expense** categories only.

Expense taxonomy (**OPEN**: confirm with both partners):

| Top-level | Subcategories |
|---|---|
| Transportation | Fuel, Tolls & parking, Maintenance & parts, General |
| Groceries & Household | General (default for any supermarket / Costco / Walmart run), Groceries, Household goods, Personal care |
| Dining & Going Out | Restaurants, Fast food, Coffee, Bars & entertainment, General |
| Recurring | Subscriptions, Bills, General |
| Discretionary | General, Fees & Interest, plus any the partners want |

Income taxonomy *(v2, **OPEN**: confirm)*:

| Top-level | Subcategories |
|---|---|
| Income | Paycheck, Interest, Refunds & reimbursements, General |

Notes:
- "Gas" in the old spreadsheet becomes Transportation.
- "Other" splits into Recurring (predictable, committed) and Discretionary (behavior-driven), because they behave differently in a budget.

### import_batches
`id`, `account_id` *(v2, was `card_id`)*, `filename`, `file_sha256`, `imported_at`, `rows_in_file`, `rows_inserted`, `rows_duplicate`, `rows_excluded`, `reverted_at` (NULL unless undone).

Original CSV files are copied to an archive directory on the Pi, named by batch id.

### transactions
| Column | Notes |
|---|---|
| `id` | |
| `source` | `import` or `manual` *(v2: was `card` / `manual`)* |
| `account_id` | *(v2)* **required**. Manual cash spending goes in the Cash account |
| `kind` | *(v2)* `expense`, `income`, `transfer`, or NULL = not yet known (goes to Review). A refund is a negative `expense` |
| `kind_source` | *(v2)* `rule`, `parser`, `manual`, or NULL. Manual is never overwritten |
| `txn_date` | NULL if the CSV doesn't provide it |
| `posted_date` | NULL for manual entries |
| `effective_date` | generated: `COALESCE(txn_date, posted_date)`. Used for all month/budget attribution |
| `amount_cents` | BIGINT, sign convention in section 3 |
| `raw_description` | exactly as the bank wrote it |
| `merchant` | cleaned display name |
| `category_id` | subcategory, for `expense`/`income` only; NULL = uncategorized (Review) |
| `category_source` | `rule`, `manual`, or NULL |
| `rule_id` | which rule assigned it, if any |
| `transfer_id` | *(v2)* for `transfer` only: links the two legs; NULL = unmatched (Review) |
| `purchaser_id` | defaults to the account owner (may be Joint); editable |
| `note` | free text (e.g., what an Amazon order was) |
| `fingerprint` | UNIQUE; see dedup below |
| `import_batch_id` | NULL for manual entries |
| `raw_row` | JSONB copy of the original CSV row |
| `created_at`, `updated_at` | |

Rules the database enforces:
- A `transfer` has no category.
- An `expense`/`income` row has no transfer link.
- A category's kind must match the transaction's kind.

### transfers *(v2)*
`id`, `matched_by` (`auto`, `rule`, `manual`), `created_at`, `note`.

- Exactly two transactions point at a transfer.
- The two legs are in **different accounts** and their amounts **sum to zero**. This is enforced by a database check at commit, like split totals.
- Examples:
  - Checking → savings: checking +50000, savings −50000.
  - Paying the Citi card from checking: checking +158782, Citi −158782.

### splits (optional per transaction)
`id`, `transaction_id`, `amount_cents`, `category_id`, `note`.

- Allowed on `expense` and `income` rows. When a transaction has splits, reports use the split lines instead of the parent's category.
- Split amounts must sum exactly to the parent amount (enforced in the app and the database).

### rules
`id`, `priority`, `match_type` (`contains`, `exact`, `regex`), `pattern` (matched against `raw_description`, case-insensitive), optional `account_id` *(v2, was `card_id`)*, optional amount range, `active`, `created_from_transaction_id`.

A rule sets one of the following *(v2)*:
- a `category_id` (which implies kind `expense` or `income`); or
- kind `transfer` plus an optional `counterpart_account_id`, e.g. "CITI AUTOPAY" in checking → transfer with the Citi card account. This helps the matcher.

Plus an optional `merchant_name` (cleaned name to apply).

### budgets
`id`, `category_id` (top-level, expense), `monthly_amount_cents`, `effective_from` (first month it applies). The budget for a month is the row with the latest `effective_from` on or before that month, so changing a budget never rewrites history.

**OPEN:** budget amounts, especially Dining & Going Out.

### Later phase: IOU ledger (phase 13)
- `iou_entries`: `id`, optional `transaction_id`, `owed_by`, `owed_to`, `amount_cents`, `date`, `note`.
- `settlements`: `id`, `from_person`, `to_person`, `amount_cents`, `date`, `note`, optional `transaction_id` (e.g. a Zelle between partners).
- IOUs and settlements **never** affect spending totals or budgets. Nothing is tracked unless someone deliberately flags it.

## 5. Import pipeline

1. User uploads one or more CSVs on the Import page and picks (or the app detects) which account each belongs to.
2. The account's parser converts each row to the standard record. Each parser handles its own institution's sign convention, date format and column names. Where the institution says what a row is, the parser may set `kind` (`kind_source = parser`):
   - Card payment received on a credit card → `transfer` *(v2: v1 excluded these; now they are stored as the card's leg of the payment)*.
   - Purchase, refund, interest, fee → `expense` (interest and fees count as spending, decided 2026-11-01).
   - Pending/unposted rows and non-transaction lines are **excluded** (not stored, only counted).
   - Rewards / cash-back credits: **OPEN**. Income, or a negative expense?
   - Balance transfers between cards: `transfer`.
3. If the CSV has a running-balance column, the last balance per date is saved as a `balance_snapshot` (`source = import`) *(v2)*.
4. **Deduplication:** fingerprint = hash of `account_id`, dates, `amount_cents`, normalized `raw_description`, and an occurrence index. The occurrence index distinguishes legitimate identical rows (two same-price coffees on one day): the nth identical row in a file gets index n. Insert with `ON CONFLICT (fingerprint) DO NOTHING`, so overlapping imports are harmless.
5. Merchant cleanup: generic cleanup (strip processor prefixes like `SQ *`, `TST*`, trailing store numbers) then rule-provided names.
6. Rules run on new rows (section 6), then the transfer matcher (section 6a).
7. Preview screen shows counts (new / duplicate / excluded / needs review) and the new rows before the user confirms. Nothing is written until confirmation.
8. A batch can be undone from the app. This deletes the rows it inserted; a transfer whose leg is deleted becomes unmatched.

Parsers live in one module per institution with a common interface, plus a test for each using a sample CSV (with fake amounts and merchants).

Institutions so far: Citi (card), Chase (card). Bank (checking/savings) institutions: **OPEN**. Need a header row and sample rows for each, including a debit purchase, a deposit/paycheck, a transfer, a card payment, and the balance column if any.

### 5a. PINNED: special transaction types
These are in scope, but each gets its own design pass and a dedicated review flow, because getting them wrong distorts totals. Until designed, they land in Review with a "needs care" flag and are never auto-categorized.

- **ATM withdrawals / cash.** Likely a transfer from checking to the Cash account. Cash spending is then entered by hand against Cash, or written off as "cash, unspecified" spending. OPEN: how much cash tracking is wanted.
- **Venmo / Zelle / peer-to-peer.** A payment can be:
  - spending (paying for something);
  - income (being paid back);
  - a transfer to an account we track (e.g. a Venmo balance, if Venmo statements are imported);
  - money between the partners (IOU settlement, phase 13).
- **Paper checks.** The bank description is usually just "CHECK 1234", so these always need a note and a manual category.

## 6. Categorization

- Rules are applied in priority order; first match wins.
- Rules only set categories/kinds on rows where the relevant `_source` is NULL or `rule`. Manual choices are never touched.
- Re-running rules over existing data is supported (after rule edits).
- Unmatched rows go to Review. When the user categorizes one by hand, the app offers "save as rule" with a pre-filled pattern based on the cleaned merchant.

### 6a. Transfer matching *(v2)*
- Candidate pairs: a `transfer` leg (or a row whose kind is unknown) in one account, and one with the **opposite amount** in a **different account** within **N days** (default 5; **OPEN**).
- A rule's `counterpart_account_id` narrows the candidates.
- Exactly one candidate on each side → linked automatically (`matched_by = auto`).
- Several candidates, or none → the row goes to the Review "Unmatched transfers" list, where a person links it or reclassifies it.
- A transfer to/from somewhere the household doesn't track (e.g. money sent to a relative) is **not** a transfer. It's an expense or income with a category.

## 7. Reporting

Implement as Postgres views where practical:

- **Spending lines view:** `expense` rows (unsplit transactions plus split lines) in one shape (date, amount, subcategory, top-level category, merchant, account, purchaser, note). All spending reports read from this; transfers never appear in it.
- **Monthly totals** by top-level category and subcategory, by `effective_date` month.
- **Budget pace:** prorated budget on a given day = monthly budget × (day of month ÷ days in month). Compare month-to-date spending against it.
- **Running monthly total** per category, resetting each month.
- *(v2)* **Cash flow:** per month, income vs. expense and the difference (saved / overspent). Transfers are excluded.
- *(v2)* **Account balances:** computed per account over time (opening balance + transactions, using the sign convention).
- *(v2)* **Net worth:** assets − liabilities, as of any date.
- *(v2)* **Reconciliation:** for each balance snapshot, the computed balance on that date and the difference. A non-zero difference means a missing or duplicated row, or a wrong opening balance. It's shown prominently.

**OPEN:** annual subscriptions: count in the month charged (simpler, default) or spread across 12 months?
**OPEN:** handling lumpy expenses like car maintenance: separate budget line, or excluded from the monthly pace view?

## 8. User interface (Streamlit)

1. **Import:** drag-and-drop CSVs, preview, confirm; list of past batches with undo.
2. **Review:** the queue of rows needing a decision, in an editable table:
   - **Uncategorized:** choose a category or mark as a transfer; "save as rule" checkbox.
   - *(v2)* **Unmatched transfers:** link to the other leg, or reclassify.
   - *(v2)* **Needs care:** the PINNED types from section 5a.
3. **Transactions:** full table with filters (month, category, account, kind, purchaser), inline editing of category, kind, purchaser and note, a split editor, and a manual-entry form (choose the account; Cash by default). Filtering by one top-level category reproduces the old per-sheet layout, with columns: date, amount, running monthly total, merchant, account, budget-to-date, note, purchaser.
4. **Dashboard:**
   - per-category spending vs. budget pace for the current month;
   - month-over-month trend;
   - drill-down from top-level to subcategory;
   - *(v2)* cash flow, account balances and net worth, and reconciliation warnings.
5. **Accounts** *(v2)*: list accounts with computed balance, last bank-reported balance and the reconciliation difference. Add or edit accounts (type, owner, opening balance) and enter balance snapshots by hand.
6. **Settings:** budgets, people, categories, rules.

Access control: a simple shared password on the app, since anyone on the home network could otherwise reach it.

## 9. Deployment and operations

- Postgres listens only on localhost (the app runs on the same Pi). The app uses its own database user with access to this database only. Credentials live in a config/secrets file excluded from git.
- Streamlit runs as a systemd service that starts on boot.
- **Backups (required):** nightly `pg_dump` via a scheduled job, copied off the Pi (another machine or cloud storage), with a retention policy. Document and test the restore procedure.
- Recommended: boot the Pi from a USB SSD rather than an SD card.
- *(v2)* Recommended: a host firewall (`ufw`) on the Pi allowing the app port only from the home network, since bank data is more sensitive.
- Optional: Tailscale for access away from home. Do not expose the app to the public internet.
- Schema changes are made through versioned migration scripts, not by hand.

## 10. Build phases

Done (v1):
1. Database schema, migrations, seed data.
2. First parser (Citi) and command-line import.
3. Import page with preview, dedup and undo.
4. Rules engine and Review page.
5. Transactions page with editing, splits and manual entry.
6. Budgets and Dashboard.
7. Deployment on the Pi: service, password, backups.

Next (v2), one phase at a time with review after each:

8. **Accounts foundation:**
   - migrate `cards` → `accounts` and add the Joint person and the Cash account;
   - add transaction `kind`, required `account_id`, category `kind` and the Income categories, `transfers`, `balance_snapshots` and opening balances;
   - migrate existing rows: card rows → `expense`, manual rows → the Cash account;
   - change the Citi/Chase parsers to store card payments as transfer legs. Re-importing old files backfills them; dedup keeps this safe.
9. **First bank parser(s)** for checking/savings, including balance columns → snapshots, and rules that set kind / transfer counterpart.
10. **Transfer matching** (section 6a) and the Review tabs (Unmatched transfers, Needs care).
11. **Reports and Accounts page:** cash flow, balances, net worth, reconciliation; Dashboard additions; Accounts page.
12. **PINNED special types** (section 5a): ATM/cash, Venmo/Zelle, checks. Each designed and reviewed before building.

Later:

13. IOU ledger; optional migration of history from the old spreadsheet.
14. Investment accounts (value-only via balance snapshots; contributions as transfers).

## 11. Open questions (summary)

- Bank institutions and sample CSVs (include a debit purchase, deposit/paycheck, transfer, card payment, balance column).
- **Credits:** confirm refunds for each issuer with a real row; how a fee/interest reversal is treated (likely a negative expense).
- Rewards / cash-back credits: income or negative expense?
- Transfer matching window (default 5 days).
- Final expense taxonomy and the income taxonomy (both partners).
- Budget amounts, especially dining.
- Annual subscriptions: month charged vs. spread across the year.
- How to treat lumpy expenses like car maintenance.
- How much cash tracking is wanted (ATM withdrawals, PINNED).
- Whether to import historical data from the existing spreadsheet.
- Whether access away from home is needed.
