-- 0007: keep the parser's note on what a row is (e.g. "card payment",
-- "direct deposit", "rewards credit") with the transaction.
--
-- Anything that posts in real currency is now stored, rewards credits
-- included (decided 2026-11-14). On a credit card, rows of unknown kind
-- normally become expenses, but a rewards credit must stay unknown (Review)
-- until income vs negative expense is decided. The hint lets the rules
-- engine tell the two apart when a rule lets go of a row. NULL for manual
-- entries and for rows imported before this migration.

ALTER TABLE transactions ADD COLUMN parser_hint TEXT;
