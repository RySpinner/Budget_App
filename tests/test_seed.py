from budget.seed import CATEGORIES_FILE, PROJECT_ROOT, load_toml, seed_categories, seed_people_and_cards


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


def test_people_and_cards_from_example_file(conn):
    data = load_toml(PROJECT_ROOT / "seed" / "local.example.toml")
    assert seed_people_and_cards(conn, data) == (2, 2)
    assert seed_people_and_cards(conn, data) == (0, 0)
    owner = conn.execute(
        "SELECT p.name FROM cards c JOIN people p ON p.id = c.owner_person_id WHERE c.last4 = '1111'"
    ).fetchone()[0]
    assert owner == "Partner B"
