# TODO

Reminders and open items. Requirements questions also live in SPEC.md section 11;
decisions to reassess are in DECISIONS.md.

## Reminders for the owner
- [ ] **Credits.** A real **payment** row is confirmed and handled (AUTOPAY). Still needed: a real **refund** row (fake the amount and merchant if you like). Use it to:
  - check refunds come through as negative spending (DECISIONS.md #12);
  - decide how a fee/interest *reversal* is treated. A refund (negative spending) seems likely, since fees count as spending.
  - Also worth a look if any show up in a year of data: "PAYMENT THANK YOU"-style online payments, and rewards/cash-back credits.
- [ ] **Other card issuers.** For each one, send the header row and a few sample rows (fake data is fine). One parser per issuer. Done so far: Citi, Chase.
- [ ] **Chase:** when they turn up, send a real **Return**, **Payment**, **Fee** and **Adjustment** row (fake amounts are fine) to confirm DECISIONS.md #48.
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
- [ ] Phase 8: IOU ledger; optional history import
- [ ] Settings page for categories, cards and people. No phase assigned yet; could come with or after Phase 6.

## Small things noticed
- [ ] Category names mix "Fees & Interest" (title case) with "Tolls & parking" (sentence case). Cosmetic; tidy up when the taxonomy is final.
