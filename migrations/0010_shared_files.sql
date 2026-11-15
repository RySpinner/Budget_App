-- 0010: keep a copy of small untracked config files (today: seed/local.toml)
-- in the database, so several development machines sharing one database can
-- stay in step. See budget/local_sync.py.
--
-- The content is plain text and can hold personal details (names, last 4
-- digits), like the people and accounts tables already do. Secrets such as
-- config/secrets.toml (database password, login hash) must NEVER be stored
-- here: the app needs them before it can reach the database at all.

CREATE TABLE shared_files (
    name       TEXT PRIMARY KEY,
    content    TEXT NOT NULL,
    sha256     TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
