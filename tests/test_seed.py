from budget.seed import CATEGORIES_FILE, PROJECT_ROOT, load_toml, seed_categories, seed_people_and_accounts


def test_category_seed_is_idempotent(conn):
    data = load_toml(CATEGORIES_FILE)
    first = seed_categories(conn, data)
    assert first > 0
    assert seed_categories(conn, data) == 0


def test_every_top_level_has_general(conn):
    seed_categories(conn, load_toml(CATEGORIES_FILE))
    missing = conn.execute(
        """
        SELECT t.name FROM categories t
        WHERE t.parent_id IS NULL
          AND NOT EXISTS (SELECT 1 FROM categories s WHERE s.parent_id = t.id AND s.name = 'General')
        """
    ).fetchall()
    assert missing == []


def test_general_added_when_left_out(conn):
    seed_categories(conn, {"category": [{"name": "No General Listed", "subcategories": ["Thing"]}]})
    names = {r[0] for r in conn.execute(
        "SELECT s.name FROM categories s JOIN categories t ON s.parent_id = t.id WHERE t.name = 'No General Listed'"
    )}
    assert names == {"Thing", "General"}


def test_people_and_accounts_from_example_file(conn):
    data = load_toml(PROJECT_ROOT / "seed" / "local.example.toml")
    assert seed_people_and_accounts(conn, data) == (2, 3, [])
    assert seed_people_and_accounts(conn, data) == (0, 0, [])
    rows = conn.execute(
        "SELECT a.display_name, a.account_class, a.account_type, p.name, a.parser_key "
        "FROM accounts a JOIN people p ON p.id = a.owner_person_id WHERE a.last4 IN ('1111', '2222') ORDER BY a.last4"
    ).fetchall()
    assert rows == [
        ("Partner B - Chase card", "liability", "credit_card", "Partner B", "chase"),
        ("Joint checking", "asset", "checking", "Joint", None),
    ]


def test_old_card_entries_still_work(conn):
    data = {"person": [{"name": "Old Style"}],
            "card": [{"display_name": "Old Card", "issuer": "Citi", "last4": "4444", "owner": "Old Style",
                      "parser_key": "citi"}]}
    assert seed_people_and_accounts(conn, data) == (1, 1, [])
    assert conn.execute("SELECT institution, account_type FROM accounts WHERE display_name = 'Old Card'"
                        ).fetchone() == ("Citi", "credit_card")


def test_changed_account_warns_and_is_not_updated(conn):
    data = load_toml(PROJECT_ROOT / "seed" / "local.example.toml")
    seed_people_and_accounts(conn, data)

    data["account"][1]["parser_key"] = "citi"
    _, added, warnings = seed_people_and_accounts(conn, data)
    assert added == 0
    assert len(warnings) == 1
    assert "parser_key = 'chase'; local.toml says 'citi'" in warnings[0]
    key = conn.execute("SELECT parser_key FROM accounts WHERE last4 = '1111'").fetchone()[0]
    assert key == "chase"


def test_income_categories_seeded_as_income(conn):
    seed_categories(conn, load_toml(CATEGORIES_FILE))
    kinds = dict(conn.execute(
        "SELECT s.name, s.kind FROM categories s JOIN categories t ON t.id = s.parent_id WHERE t.name = 'Income'"
    ).fetchall())
    assert kinds["Paycheck"] == "income" and set(kinds.values()) == {"income"}
