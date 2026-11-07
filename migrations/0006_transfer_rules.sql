-- 0006: rules that mark rows as transfers (phase 9, part 2).
--
-- A rule now sets ONE of:
--   - a category (which implies kind expense or income), as before; or
--   - kind "transfer" (is_transfer), optionally naming the account on the
--     other side (counterpart_account_id), e.g. "Pmt to card" in checking ->
--     transfer with the BCU card. The counterpart is a hint for transfer
--     matching (phase 10); the rule itself never links two rows.

ALTER TABLE rules ALTER COLUMN category_id DROP NOT NULL;
ALTER TABLE rules ADD COLUMN is_transfer BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE rules ADD COLUMN counterpart_account_id INTEGER REFERENCES accounts (id);

ALTER TABLE rules ADD CONSTRAINT rules_sets_one_thing CHECK (
    (is_transfer AND category_id IS NULL)
    OR (NOT is_transfer AND category_id IS NOT NULL AND counterpart_account_id IS NULL)
);
ALTER TABLE rules ADD CONSTRAINT rules_counterpart_is_another_account CHECK (
    counterpart_account_id IS NULL OR account_id IS NULL OR counterpart_account_id <> account_id
);

-- A transaction may now carry a rule_id because a rule made it a transfer
-- (no category), not only because a rule chose its category.
ALTER TABLE transactions DROP CONSTRAINT transactions_rule_id;
ALTER TABLE transactions ADD CONSTRAINT transactions_rule_id CHECK (
    rule_id IS NULL OR category_source = 'rule' OR (category_id IS NULL AND kind = 'transfer')
);
