# Decisions

Calls made while building that SPEC.md didn't settle. SPEC.md stays the source of
truth for requirements; this file records *how* things were decided and what is
worth a second look.

- **Review** = made by Claude, not yet confirmed by the owner. Reassess when convenient.
- **Decided** = confirmed by the owner (date in brackets).

When an item is confirmed or changed, move it to *Decided* (or update SPEC.md if it
changes a requirement). Newest items go at the end of each section.

---

## Review (made by Claude, not yet confirmed)

### Data model and database (Phase 1)
1. **Migrations:** plain numbered SQL files plus a small runner (`budget/migrate.py`), not Alembic. Chosen for readability.
2. **Rules enforced by the database itself:**
   - Categories are limited to two levels.
   - Transactions, splits and rules must point at a subcategory.
   - Budgets go on top-level categories, and `effective_from` must be the 1st of a month.
   - Card rows must have a card, an import batch and a fingerprint. Manual rows must have none of these and no posted date.
   - `category_id` and `category_source` are both set or both empty.
3. **Manual-category protection is also a database trigger.** A rule can never overwrite a `manual` category, even if app code has a bug.
4. **Split totals are checked by a deferred database trigger**, at commit, so splits can be replaced within one transaction. Changing the parent amount is checked too.
5. **Manual entries have no fingerprint** (NULL). Dedup only applies to card rows.
6. **`import_batches.file_sha256` is not unique.** Re-importing a file after undoing it is allowed; the app warns "already imported" instead.
7. **PostgreSQL 15 or newer is required.** The schema uses `UNIQUE NULLS NOT DISTINCT`. 17 is recommended to match current Raspberry Pi OS.
8. **The seed only inserts, never updates.** If `seed/local.toml` disagrees with an existing card, it prints a warning. Existing rows are changed in the database (and later in a Settings page).

### Importing (Phases 2–3)
9. **Excluded rows are not stored as transactions.** Only their count is saved, in `import_batches.rows_excluded`. The rows themselves survive only in the archived CSV.
10. **Citi's single `Date` column is treated as the transaction date.** `posted_date` stays NULL for Citi rows.
11. **Every Citi Credit becomes a negative amount,** whatever sign Citi uses.
12. **Citi exclusion patterns:** payments match `AUTOPAY`, `AUTO-PMT`, or `PAYMENT … THANK YOU`; then `BALANCE TRANSFER`, then rewards wording. Pending rows (Status = Pending) are excluded. The AUTOPAY pattern is confirmed by a real row; the others, and the refund format, are still guesses (see TODO.md, "Credits").
13. **Merchant cleanup:**
    - strips processor prefixes (`SQ *`, `TST*`, …), masked card numbers, `null`, and trailing numbers and state codes;
    - removes `#1234` store numbers;
    - keeps the bank's capitalisation.
14. **Archived files** are named `<batch id, 6 digits>_<original filename>`. The default folder is `archive/` in the project, configurable in `secrets.toml`.
15. **Undoing an import deletes its rows**, including any categories, notes or splits added since. The page warns first. The archived file is kept.
16. **Card auto-detection** happens only when exactly one active card's parser can read the file. The masked number in Citi descriptions is NOT used (see Decided).

### Rules and review (Phase 4)
17. **Rule order:** lower priority number first, then oldest rule first; the first match wins.
18. **Matching:**
    - `contains` and `exact` ignore case and treat runs of spaces as one.
    - `regex` uses Python syntax and ignores case.
19. **Re-running rules after edits** moves each rule-categorized row to whichever rule now matches it, or back to Review if none matches. Manual rows are never touched. Merchant names a rule set earlier are *not* reverted.
    - *Alternative:* keep the old category when no rule matches any more.
20. **A rule's `merchant_name` replaces the cleaned merchant name**, both at import and when rules are re-run.
21. **"Save as rule" from Review:**
    - The suggested pattern is the longest run of the merchant's leading words that appears in the bank description.
    - New rules get priority 100.
    - The new rule is applied only to rows still uncategorized.
22. **Categories chosen in Review or Transactions count as `manual`.**

### Transactions page (Phase 5)
23. **The `spending_lines` view and budget functions were built in Phase 5** (migration 0002), not Phase 6 as SPEC lists, because the Transactions page needed them.
24. **Budget-to-date** = monthly budget × day ÷ days in month, using exact decimals and rounded to the nearest cent.
25. **Running totals follow the filters.** With a card, purchaser or subcategory filter on, they add up only the rows shown, and the page says so.
26. **The Transactions page opens on the current month**, which may be empty early in a month.
27. **A split needs at least two lines.** Split transactions are left out of the Review queue, because their categories live on the split lines.
28. **Manual entries** use the typed description as both `raw_description` and `merchant`.

### Budgets and Dashboard (Phase 6)
31. **Annual subscriptions and lumpy expenses (both OPEN in SPEC §7)** currently get no special handling. Every charge counts in the month it happened, which is SPEC's "simpler, default" for subscriptions. Nothing is left out of the pace view. Revisit once these are decided (TODO.md).
32. **Budget status** per category:
    - *Over budget* if spent > monthly budget;
    - else *Over pace* if spent > budget-to-date;
    - else *On pace*;
    - *No budget* if none is set.
    It's shown with an icon and a label, never color alone.
33. **Pace is measured as of:** today for the current month, the last day for past months, and not at all for future months.
34. **"Spent this month"** is the month's total so far, including uncategorized spending. The "Budgeted categories vs pace" headline only counts categories that have a budget.
35. **Chart amounts:** charts get integer cents and turn them into "$" axis labels inside the chart (in the browser). Tooltips use the exact text from `format_cents`, so no money is converted to floating point in Python.
36. **Colors:** each top-level category keeps a fixed color, based on its position in the category order, and Uncategorized is gray. The palette passed the colorblind-safety checker in both light and dark mode. Every chart has a table view, as the checker's contrast warning requires.
37. **Budget changes apply "from month X onward"** and can be set for any month from 12 back to 12 ahead. Deleting a history row makes the previous amount apply again.
38. **The Dashboard is the app's start page.** Import moved to `/import`.

### Citi parsing fixes
39. **Reading Citi's unquoted CSV:** each row is read from both ends.
    - Status and date come from the left.
    - The amount comes from the right, gluing split thousands groups back on: ` -$ 1` + `878.69` becomes −$1,878.69, and the Excel version `($1)` + `587.82` becomes −$1,587.82.
    - An empty field just before the amount means it was in the Credit column.
    - Everything in between is the description, so commas inside descriptions also work.
    - Parentheses or a minus sign mean negative.
    - *Known blind spot:* a description whose last comma-separated piece is a short number (e.g. `STORE, 12`) followed by an amount like `345.00` would be misread as `12,345.00`. This is unlikely; revisit if it ever shows up.
40. **`raw_row` for Citi** keeps the fields exactly as read, under the five column names, plus any overflow in order under `_extra`. Nothing in it is re-joined.

### Deployment (Phase 7)
41. **Login:**
    - The password is stored only as a salted PBKDF2 hash (Python standard library).
    - "Remember me" is a signed token (expiry + HMAC) in a cookie that lasts 30 days. A tiny browser script writes the cookie, because Streamlit can't set cookies itself.
    - The cookie is readable by page scripts (not HttpOnly). That's acceptable for a home app; the token holds no password and stops working when the password changes.
    - A wrong password waits 1 second, to slow down guessing.
42. **The password is required by default.** The app refuses to run without one unless `require_password = false` is set, which is meant only for a development laptop.
43. **Page files moved from `app/pages/` to `app/views/`.** Streamlit automatically lists a `pages/` folder before the login check runs, which let logged-out visitors open pages. A browser test confirms every page URL now shows only the login screen.
44. **Backup retention:**
    - 14 daily and 12 monthly dumps, on the Pi and mirrored to the remote (`rsync --delete`).
    - Original CSVs are only ever added to the remote copy, never deleted.
    - A run fails, so systemd shows it as failed, if the off-Pi copy fails or no destination is set.
45. **Restore testing is manual** (`restore-test.sh`, suggested every month or two), not on a timer.
46. **The backup job runs as the `budget` user**, with the database password in `~budget/.pgpass` and its own SSH key. It doesn't run as root.
47. **Systemd hardening:** `NoNewPrivileges`, `ProtectSystem=full`, `PrivateTmp`. The app binds to `0.0.0.0:8501` (LAN only, behind the router; never port-forwarded).

### Chase parser
48. **Row type comes from Chase's own Type column** ("Sale" and "Payment" confirmed with real rows; the others follow Chase's usual export):
    - Sale → purchase
    - Return → refund
    - Payment → excluded (card payment)
    - Fee → purchase (interest and fees count as spending)
    - Adjustment → purchase or refund by its sign, except rewards wording (`REDEMPTION`, `REDEEM`, `CASH BACK`, `REWARDS`), which is excluded as a rewards redemption.
49. **Chase's sign is flipped** (Chase: charges negative). Transaction Date becomes `txn_date` and Post Date becomes `posted_date`, so months follow the transaction date.
50. **Chase's own Category column is kept in `raw_row` but not used.** It could later pre-fill category suggestions in Review.
51. **A comma in a Chase description is re-joined,** on the assumption that the last four columns (Category, Type, Amount, Memo) never contain commas.

### v2 SPEC: accounts and double-entry lite (written 2026-11-06, not yet built)
52. **"Double-entry lite":**
    - Every transaction sits in one account, and its other side is either a category or another household account (a transfer).
    - A transfer has two linked legs that must sum to zero.
    - Balances are reconciled against bank-reported snapshots.
    - No journals, and no debits/credits in the UI.
53. **The v1 sign convention is kept for all accounts:** positive = against the household (money out of an asset, or more owed on a card). Existing rows need no sign migration.
54. **Card payments are stored from v2 on,** as the card's leg of a transfer, instead of being excluded (this changes #9 for payments). Re-importing old card files backfills them. Pending rows stay excluded.
55. **A built-in "Cash" account** holds hand-entered cash spending. Existing manual entries move into it.
56. **Unambiguous transfers are linked automatically** (one candidate on each side, opposite amount, different accounts, within 5 days). Everything else goes to Review. Money sent to people/places we don't track is expense or income, not a transfer.
57. **A new transaction `kind`:** expense, income or transfer, with a refund being a negative expense. It has its own `kind_source`, and manual choices are protected like categories. Parsers may set it when the bank says what a row is (e.g. Chase's Type column).
58. **Income categories** live in the same table, marked `kind = income`, starting with Income › Paycheck, Interest, Refunds & reimbursements, General. Budgets apply only to expense categories.
59. **Accounts have a class** (asset or liability) **and a type** (checking, savings, cash, credit_card; reserved: brokerage, retirement, loan). Investment accounts will be value-only, through balance snapshots.
60. **v2 phase order:** 8 accounts foundation → 9 bank parsers → 10 transfer matching → 11 reports and Accounts page → 12 PINNED special types; the IOU ledger becomes phase 13.

### Phase 8 as built (accounts foundation)
61. **Existing data:** card ids are kept as account ids, so old import fingerprints still match. All existing rows become `expense` (kind source `parser` for imported rows, `manual` for hand-entered ones), and manual rows move into the Cash account.
62. **Re-importing old card files is safe and fills in the card payments,** which are now stored as transfer legs. Everything already imported shows up as a duplicate.
63. **Rewards credits are still excluded** until SPEC's open question (income or negative expense) is decided. Pending rows are excluded as before.
64. **A manual entry without a category is recorded as an expense** awaiting a category, not as "kind unknown", because the form is mostly for cash spending.
65. **Choosing a category sets the kind:** an income category makes the row income. "Transfer between our accounts" is offered as a choice next to the categories in Review and Transactions, rather than as a separate Kind field. Saving a *rule* for a transfer comes in phase 9.
66. **Categorizing a linked transfer leg by hand dissolves the link,** and the other leg goes back to unmatched. Deleting a leg (e.g. undoing an import) does the same.
67. **The Transactions page shows spending by default,** with a Kind filter for income, transfers, unknown or everything. Running totals add up whatever is shown.
68. **`local.toml` uses `[[account]]` entries** (display_name, institution, type, last4, owner, optional parser_key). Old `[[card]]` entries still work as credit cards.

### Not yet addressed
30. **Categories, cards and people** can only be edited with SQL or the seed file for now. SPEC's Settings page has no phase assigned.

---

## Decided (confirmed by the owner)

- **[2026-11-01] Starting taxonomy:** seed the SPEC §4 table as-is. The taxonomy itself is still OPEN until both partners confirm.
- **[2026-11-01] People and cards** live in the untracked `seed/local.toml`. The committed example file uses placeholders.
- **[2026-11-01] Interest and fees count as spending** (not excluded). SPEC §5 updated.
- **[2026-11-01] "Fees & Interest" is a subcategory under Discretionary**, "for now". Revisit if it causes headaches.
- **[2026-11-01] The masked card number in Citi descriptions** (`XXXXXXXXXXXX1234`) is probably a per-merchant virtual number, not the card's last 4. Don't use it to identify the card.
- **[2026-11-01] The trailing blank rows in the Citi sample** came from hand-editing the file. The parser skips blank rows anywhere anyway.
- **[2026-11-01] Development laptop is Windows (PowerShell).** Docs give Windows and Mac/Linux commands.
- **[2026-11-01] Seed warns** when `local.toml` disagrees with an existing card.
- **[2026-11-01] Citi's real CSV format** (as downloaded, viewed in Notepad): descriptions are quoted, amounts are not. A credit looks like ` -$ 1,878.69`, and its thousands comma splits it across two columns. Handle it in the parser (#39) rather than asking the owner to clean files.
- **[2026-11-01] Files opened and saved in Excel change format:** amounts become `($1,587.82)`-style (also split), quotes are dropped, the header gains a trailing comma, and dates lose leading zeros. The parser accepts both, and both give the same fingerprints. Still, import the original download, not an Excel-saved copy.
- **[2026-11-06] Scope extends to checking and savings accounts** (v2), with income, transfers, balances and net worth. SPEC.md rewritten; built before the IOU ledger.
- **[2026-11-06] A "Joint" person** owns joint accounts and is the purchaser of joint purchases.
- **[2026-11-06] Bookkeeping style:** thorough, "double-entry" style, but not overwhelming for household use (interpreted as #52).
- **[2026-11-06] "Accounts" are generic, with subtypes,** so investment accounts can be added later. Bank accounts are the focus now.
- **[2026-11-06] Venmo/Zelle, ATM withdrawals and paper checks are in scope but PINNED:** designed separately (SPEC §5a and phase 12), with extra review.
- **[2026-11-01] Chase CSV format** (real download): `Transaction Date,Post Date,Description,Category,Type,Amount,Memo`. Unix line endings, no quotes, no `$` or thousands commas; charges are negative (e.g. `-8.88`) with Type `Sale`.
- **[2026-11-01] Chase autopay rows** look like `AUTOMATIC PAYMENT - THANK`, with Type `Payment`, a positive amount and an empty Chase category. They're excluded as card payments (confirmed with a real row).
- **[2026-11-01] Pi:** Raspberry Pi 5 running Raspberry Pi OS 64-bit.
- **[2026-11-01] Off-Pi backups** go to a Mac / Linux machine / NAS, pushed nightly by the Pi with rsync over SSH.
- **[2026-11-01] Access away from home: not sure yet.** Home network only for now; Tailscale steps are written up as optional (docs/deploy.md §12).
- **[2026-11-01] Login is remembered for 30 days** per browser.
- **[2026-11-01] Citi autopay rows** look like `AUTOPAY <digits>RAUTOPAY AUTO-PMT`, with the amount in Credit as a negative. They're excluded as card payments.
