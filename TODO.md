# TODO

Reminders and open items. Requirements questions also live in SPEC.md section 11;
decisions to reassess are in DECISIONS.md.

## Reminders for the owner
- [ ] **Credits.** A real **payment** row is confirmed and handled (AUTOPAY). Still needed: a real **refund** row (fake the amount and merchant if you like). Use it to:
  - check refunds come through as negative spending (DECISIONS.md #12);
  - decide how a fee/interest *reversal* is treated. A refund (negative spending) seems likely, since fees count as spending.
  - Also worth a look if any show up in a year of data: "PAYMENT THANK YOU"-style online payments, and rewards/cash-back credits.
- [ ] **Review DECISIONS.md #52–68** (v2 SPEC interpretations and phase 8 as built).
- [ ] **On your laptop after pulling phase 8:** run `python -m budget.migrate`, then re-import your card CSVs to fill in the card payments (old rows show up as duplicates).
- [ ] **More BCU checking rows** (fake amounts are fine): a debit card purchase, a transfer to/from savings, an ATM withdrawal, a check, a Venmo/Zelle row, a fee and a pending row if the export shows them. Right now only the paycheck and the card payment are confirmed. Also: is savings at BCU too (same export)?
- [ ] **After pulling phase 9:** run `python -m budget.migrate` (0005 and 0006). Then, in Review, try marking a BCU checking row as a transfer with *Save as rule* and an *Other account*.
- [ ] **Review DECISIONS.md #81–95** (phase 9).
- [ ] **More SoFi checking rows** (fake amounts are fine; copy from Notepad, not Excel): a paycheck/direct deposit, a debit card purchase, a card payment, a transfer to/from savings (including SoFi Vaults), an ATM withdrawal, interest earned, a pending row, and any amount of $1,000 or more. The Type and Status values matter most (DECISIONS.md #96–100). Also: does SoFi savings use the same export?
- [ ] **Decide:** should bank amounts show money in as positive on screen (DECISIONS.md #87)?
- [ ] **Bank samples** for each checking/savings institution: header row plus a debit purchase, a deposit/paycheck, a transfer to savings, a card payment, an ATM withdrawal, and the balance column if there is one (fake amounts are fine; copy from Notepad, not Excel).
- [ ] **After pulling the rewards change:** run `python -m budget.migrate` (0007), then re-import your card CSVs. Past rewards credits get added (and wait in Review); everything else shows as a duplicate.
- [ ] **After pulling phase 10:** run `python -m budget.migrate` (0008). Then on the Rules page press **Re-run all rules** once: it flags older ATM / Venmo / Zelle / check rows as "needs care" and links the card payments already in the database. What's left shows in Review → Unmatched transfers.
- [ ] **After pulling phase 11a:** run `python -m budget.migrate` (0009). Then on the **Accounts** page, for each account:
  - set an opening balance (for checking, "Use the bank's earliest balance" does it in one click);
  - for each card, type in a recent statement balance;
  - where the balance matches, press "Mark reconciled through".
- [ ] **Fix manual income entries made before 2026-11-15** (e.g. interest in the test savings account). They were stored as money out (DECISIONS.md #138). On the Transactions page, set Kind → Income and pick the account, choose each entry, delete it and add it again. The Accounts page difference should then go to $0.
- [ ] **Review DECISIONS.md #132–137** (phase 11b as built). No migration needed for 11b.
- [ ] **Review DECISIONS.md #122–131** (phase 11a as built).
- [ ] **Review DECISIONS.md #108–120** (phase 10 as built).
- [ ] **Review DECISIONS.md #101–107** (double-entry review: generated legs, equity entries, posted-date balances, reconciled-through, books-balance check).
- [ ] **Decide:** rewards / cash-back credits as income or as negative expense (they'll be stored and wait in Review meanwhile); the transfer matching window (default 5 days); how much cash tracking is wanted; the income categories.
- [ ] **Other card issuers.** For each one, send the header row and a few sample rows (fake data is fine). One parser per issuer. Done so far: Citi, Chase, BCU card, Amex. Bank accounts so far: BCU checking, SoFi checking.
- [ ] **Chase:** Payment confirmed. When they turn up, send a real **Return**, **Fee** and **Adjustment** row (fake amounts are fine) to confirm DECISIONS.md #48.
- [ ] **BCU card:** payment and purchase confirmed. When they turn up, send a real **refund**, **rewards redemption**, **interest/fee** row, and say whether **pending** charges appear in the export (DECISIONS.md #69–75). If your credit union checking/savings export has the same columns, say so: phase 9 can reuse this parser.
- [ ] **Amex:** payment, purchase and a streaming statement credit confirmed. Benefit credits are refunds (decided). When they turn up, send a real **return**, **Membership Rewards** and **interest/fee** row.
- [ ] *Idea:* use Chase's own Category column to pre-fill suggestions on the Review page (DECISIONS.md #50).
- [ ] **Category taxonomy:** confirm with your partner (SPEC §4, OPEN).
- [ ] **Budget amounts**, especially Dining & Going Out (SPEC §4, OPEN). Enter them on the Budgets page.
- [ ] **Annual subscriptions:** count in the month charged (what happens now), or spread across 12 months? (SPEC §7, OPEN; DECISIONS.md #31)
- [ ] **Lumpy expenses** such as car maintenance: a separate budget line, or left out of the monthly pace view? Nothing is left out yet. (SPEC §7, OPEN; DECISIONS.md #31)
- [ ] **Old spreadsheet history:** import it or not? (SPEC §11)
- [ ] **Set up the Pi** following docs/deploy.md: service, password, backups to your Mac/Linux/NAS, and run `restore-test.sh` once.
- [ ] **Access away from home:** decide whether it's needed. If so, follow the Tailscale section in docs/deploy.md. (SPEC §11)
- [ ] Look over the **Review** list in DECISIONS.md.

## Build
- [x] Phase 1: schema, migrations, seed data
- [x] Phase 2: Citi parser, command-line import
- [x] Phase 3: Import page (preview, dedup, undo)
- [x] Phase 4: rules engine, Review and Rules pages
- [x] Phase 5: Transactions page (filters, editing, splits, manual entry)
- [x] Phase 6: budgets and Dashboard
- [x] Phase 7: deployment on the Pi (service, password, backups). Code and guide done; the owner still does the Pi setup.
- [x] Phase 8: accounts foundation (cards → accounts, Joint, Cash account, kind, transfers, balance snapshots, card payments stored)
- [x] Phase 9: first bank parser(s), with balance snapshots and kind/transfer rules
  - [x] Part 1: BCU checking parser, balance snapshots (undone with their import), card/checking kinds
  - [x] Part 2: transfer rules with an optional counterpart account (migration 0006); saving a transfer rule from Review; Rules page "Sets" / "Other account"
- [x] Before phase 10: store currency rewards credits (kind unknown → Review) instead of excluding them; points-only rows stay excluded (migration 0007)
- [x] Phase 10: transfer matching and Review tabs (unmatched transfers, needs care); generated legs for accounts without a CSV (migration 0008)
- [x] Phase 11a: reconciliation and Accounts page (posted-date balances, balance adjustments, reconciled-through dates; migration 0009)
- [x] Phase 11b: cash flow, net worth, books-balance check; Dashboard additions
- [ ] Phase 12: PINNED special types: ATM/cash, Venmo/Zelle, checks; split lines to a category or an account; loan accounts
- [ ] Phase 13: IOU ledger; optional history import
- [ ] Phase 14 (future): investment accounts
- [ ] Settings page for categories and people (accounts are edited on the Accounts page).
- [ ] Pi firewall (`ufw`) step in docs/deploy.md (SPEC §9, recommended for v2).

## Small things noticed
- [ ] Category names mix "Fees & Interest" (title case) with "Tolls & parking" (sentence case). Cosmetic; tidy up when the taxonomy is final.
