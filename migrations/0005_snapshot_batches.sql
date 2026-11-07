-- 0005: link imported balance snapshots to the import batch that read them,
-- so undoing an import removes its balances too (phase 9).

ALTER TABLE balance_snapshots
    ADD COLUMN import_batch_id INTEGER REFERENCES import_batches (id);

ALTER TABLE balance_snapshots
    ADD CONSTRAINT balance_snapshots_import_batch
    CHECK ((source = 'import') = (import_batch_id IS NOT NULL));
