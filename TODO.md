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
- [ ] **Bank samples** for each checking/savings institution: header row plus a debit purchase, a deposit/paycheck, a transfer to savings, a card payment, an ATM withdrawal, and the balance column if there is one (fake amounts are fine; copy from Notepad, not Excel).
- [ ] **Decide:** rewards / cash-back credits as income or as negative expense; the transfer matching window (default 5 days); how much cash tracking is wanted; the income categories.
- [ ] **Other card issuers.** For each one, send the header row and a few sample rows (fake data is fine). One parser per issuer. Done so far: Citi, Chase, BCU card, Amex.
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
- [ ] Phase 9: first bank parser(s), with balance snapshots and kind/transfer rules
- [ ] Phase 10: transfer matching and Review tabs (unmatched transfers, needs care)
- [ ] Phase 11: cash flow, balances, net worth, reconciliation; Accounts page
- [ ] Phase 12: PINNED special types: ATM/cash, Venmo/Zelle, checks
- [ ] Phase 13: IOU ledger; optional history import
- [ ] Phase 14 (future): investment accounts
- [ ] Settings page for categories and people (accounts get their own page in phase 11).
- [ ] Pi firewall (`ufw`) step in docs/deploy.md (SPEC §9, recommended for v2).

## Small things noticed
- [ ] Category names mix "Fees & Interest" (title case) with "Tolls & parking" (sentence case). Cosmetic; tidy up when the taxonomy is final.
