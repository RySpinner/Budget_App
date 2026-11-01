# TODO

Reminders and open items. Requirements questions also live in SPEC.md section 11;
decisions to reassess are in DECISIONS.md.

## Reminders for the owner
- [ ] **Credits.** A real **payment** row is confirmed and handled (AUTOPAY). Still needed: a real **refund** row (fake the amount and merchant if you like). Use it to:
  - check refunds come through as negative spending (DECISIONS.md #12);
  - decide how a fee/interest *reversal* is treated. A refund (negative spending) seems likely, since fees count as spending.
  - Also worth a look if any show up in a year of data: "PAYMENT THANK YOU"-style online payments, and rewards/cash-back credits.
- [ ] **Other card issuers.** For each one, send the header row and a few sample rows (fake data is fine). One parser per issuer.
- [ ] **Category taxonomy:** confirm with your partner (SPEC §4, OPEN).
- [ ] **Budget amounts**, especially Dining & Going Out (SPEC §4, OPEN). Enter them on the Budgets page.
- [ ] **Annual subscriptions:** count in the month charged (what happens now), or spread across 12 months? (SPEC §7, OPEN; DECISIONS.md #31)
- [ ] **Lumpy expenses** such as car maintenance: a separate budget line, or left out of the monthly pace view? Nothing is left out yet. (SPEC §7, OPEN; DECISIONS.md #31)
- [ ] **Old spreadsheet history:** import it or not? (SPEC §11)
- [ ] **Raspberry Pi** model and OS. Also: is access from outside the home needed (Tailscale)? (SPEC §11)
- [ ] **CLAUDE.md "Environment":** fill in the database host, name and user once settled. Never the password.
- [ ] Look over the **Review** list in DECISIONS.md.

## Build
- [x] Phase 1: schema, migrations, seed data
- [x] Phase 2: Citi parser, command-line import
- [x] Phase 3: Import page (preview, dedup, undo)
- [x] Phase 4: rules engine, Review and Rules pages
- [x] Phase 5: Transactions page (filters, editing, splits, manual entry)
- [x] Phase 6: budgets and Dashboard
- [ ] Phase 7: deployment on the Pi (service, password, backups)
- [ ] Phase 8: IOU ledger; optional history import
- [ ] Settings page for categories, cards and people. No phase assigned yet; could come with or after Phase 6.

## Small things noticed
- [ ] Category names mix "Fees & Interest" (title case) with "Tolls & parking" (sentence case). Cosmetic; tidy up when the taxonomy is final.
