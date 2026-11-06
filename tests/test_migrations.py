import psycopg

from budget.migrate import MIGRATIONS_DIR, apply_migrations, migration_files


def test_migration_files_are_well_named():
    assert migration_files(MIGRATIONS_DIR), "expected at least one migration"


def test_all_migrations_recorded(database_url):
    with psycopg.connect(database_url) as conn:
        recorded = [r[0] for r in conn.execute("SELECT filename FROM schema_migrations ORDER BY version")]
    assert recorded == [p.name for p in migration_files()]


def test_second_run_is_a_no_op(database_url):
    with psycopg.connect(database_url) as conn:
        assert apply_migrations(conn) == []


def test_expected_tables_exist(conn):
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    ).fetchall()
    tables = {r[0] for r in rows}
    assert {
        "people", "accounts", "categories", "import_batches", "transfers", "balance_snapshots",
        "transactions", "splits", "rules", "budgets", "schema_migrations",
    } <= tables


def test_money_columns_are_bigint(conn):
    rows = conn.execute(
        "SELECT table_name, column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'public' AND column_name LIKE '%cents'"
    ).fetchall()
    assert rows
    for table, column, data_type in rows:
        assert data_type == "bigint", f"{table}.{column} is {data_type}"
