-- 0008: transfer matching and the Review tabs (SPEC.md phase 10).
--
-- * kind_source 'match': the transfer matcher decided a row of unknown kind
--   is a transfer, by linking it to the other leg. If that link is removed
--   again (an import undone, a link undone by hand), the row goes back to
--   unknown, because nothing else said it was a transfer.
-- * generated_from_id: a leg the app created in an account that has no CSV
--   (Cash, a loan, a value-only investment account) as the other half of a
--   transfer. It is deleted together with the row it was made from.
-- * needs_care: why a row needs extra care (ATM / cash, Venmo / Zelle,
--   paper check; SPEC section 5a). Such rows wait in Review's "Needs care"
--   tab and are never sorted by rules or linked automatically.
-- * no_auto_match: set when a person undoes a link, so the matcher doesn't
--   simply link the same two rows again.

ALTER TABLE transactions DROP CONSTRAINT transactions_kind_source_check;
ALTER TABLE transactions ADD CONSTRAINT transactions_kind_source_check
    CHECK (kind_source IN ('rule', 'parser', 'manual', 'match'));
-- The matcher only ever gives a row the kind "transfer".
ALTER TABLE transactions ADD CONSTRAINT transactions_match_is_transfer
    CHECK (kind_source IS DISTINCT FROM 'match' OR kind = 'transfer');

ALTER TABLE transactions ADD COLUMN generated_from_id INTEGER
    REFERENCES transactions (id) ON DELETE CASCADE;
ALTER TABLE transactions ADD CONSTRAINT transactions_generated_leg
    CHECK (generated_from_id IS NULL OR (source = 'manual' AND kind = 'transfer'));
CREATE UNIQUE INDEX transactions_generated_from_idx ON transactions (generated_from_id);

ALTER TABLE transactions ADD COLUMN needs_care TEXT;
ALTER TABLE transactions ADD COLUMN no_auto_match BOOLEAN NOT NULL DEFAULT false;

-- A row the matcher made a transfer goes back to unknown when its link goes.
-- This also runs when the link disappears because the transfer was deleted
-- (transactions.transfer_id is ON DELETE SET NULL).
CREATE FUNCTION transactions_unmatch_reverts_kind() RETURNS trigger AS $$
BEGIN
    IF OLD.transfer_id IS NOT NULL AND NEW.transfer_id IS NULL AND NEW.kind_source = 'match' THEN
        NEW.kind := NULL;
        NEW.kind_source := NULL;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER transactions_unmatch_reverts_kind
    BEFORE UPDATE OF transfer_id ON transactions
    FOR EACH ROW EXECUTE FUNCTION transactions_unmatch_reverts_kind();
