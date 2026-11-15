-- 0009: account balances and reconciliation (SPEC.md phase 11a).
--
-- * balance_date: the day a row counts toward the account balance. The
--   bank's balance follows the POSTED date, so that comes first; budgets and
--   spending keep using effective_date (transaction date first).
-- * kind 'adjustment': a balance adjustment, entered on the Accounts page to
--   close a reconciliation difference nobody can explain. It changes the
--   account balance (and so net worth) but is never spending, income or a
--   transfer, and never has a category.
-- * accounts.reconciled_through: the last day the account was checked
--   against the bank and agreed. Changes on or before it are warned about.
-- * account_balance(account, day): the computed balance at the end of a day,
--   in the account's own terms (money held for assets, money owed for
--   liabilities). Opening balance + the rows after the opening date.
-- * reconciliation view: every bank-stated balance next to the computed one.

ALTER TABLE transactions
    ADD COLUMN balance_date DATE GENERATED ALWAYS AS (COALESCE(posted_date, txn_date)) STORED;
CREATE INDEX transactions_account_balance_date_idx ON transactions (account_id, balance_date);

ALTER TABLE transactions DROP CONSTRAINT transactions_kind_check;
ALTER TABLE transactions ADD CONSTRAINT transactions_kind_check
    CHECK (kind IN ('expense', 'income', 'transfer', 'adjustment'));
-- Adjustments are always entered by hand.
ALTER TABLE transactions ADD CONSTRAINT transactions_adjustment_is_manual
    CHECK (kind IS DISTINCT FROM 'adjustment' OR (source = 'manual' AND kind_source = 'manual'));

ALTER TABLE accounts ADD COLUMN reconciled_through DATE;

-- The opening balance is the balance at the END of opening_balance_date, like
-- a bank-stated balance, so rows on or before that day are already in it.
-- Without an opening balance, the account starts from 0 before its first row.
-- Asking for a day before the opening date gives NULL (not known).
CREATE FUNCTION account_balance(p_account_id INTEGER, p_as_of DATE) RETURNS BIGINT AS $$
    SELECT CASE
             WHEN a.opening_balance_date IS NOT NULL AND p_as_of < a.opening_balance_date THEN NULL
             ELSE COALESCE(a.opening_balance_cents, 0)
                  + CASE WHEN a.account_class = 'asset' THEN -1 ELSE 1 END
                  * COALESCE((SELECT sum(t.amount_cents)
                                FROM transactions t
                               WHERE t.account_id = a.id
                                 AND t.balance_date <= p_as_of
                                 AND (a.opening_balance_date IS NULL OR t.balance_date > a.opening_balance_date)), 0)
           END::bigint
      FROM accounts a
     WHERE a.id = p_account_id
$$ LANGUAGE sql STABLE;

-- difference = what the bank says - what the rows add up to (account's own terms).
CREATE VIEW reconciliation AS
SELECT s.id AS snapshot_id,
       s.account_id,
       s.as_of_date,
       s.source,
       s.balance_cents                                           AS bank_cents,
       account_balance(s.account_id, s.as_of_date)               AS computed_cents,
       s.balance_cents - account_balance(s.account_id, s.as_of_date) AS difference_cents
  FROM balance_snapshots s;
