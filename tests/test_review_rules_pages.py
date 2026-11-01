"""Smoke tests for the Review and Rules pages (AppTest can't edit data_editor
cells, so the categorize/save logic itself is tested in test_rules.py)."""

from pathlib import Path

import psycopg
import pytest
from streamlit.testing.v1 import AppTest

from budget.importer import commit_import, preview_import
from budget.rules import create_rule
from budget.seed import CATEGORIES_FILE, load_toml, seed_categories

PAGES = Path(__file__).resolve().parent.parent / "app" / "views"
FIXTURE = Path(__file__).parent / "fixtures" / "citi" / "sample.csv"


@pytest.fixture(scope="module")
def db(committed_database_url, tmp_path_factory):
    with psycopg.connect(committed_database_url) as conn:
        seed_categories(conn, load_toml(CATEGORIES_FILE))
        person = conn.execute("INSERT INTO people (name) VALUES ('P') RETURNING id").fetchone()[0]
        card = conn.execute(
            "INSERT INTO cards (display_name, issuer, owner_person_id, parser_key) "
            "VALUES ('Citi', 'Citi', %s, 'citi') RETURNING id", (person,),
        ).fetchone()[0]
        coffee = conn.execute("SELECT id FROM categories WHERE name = 'Coffee'").fetchone()[0]
        create_rule(conn, "coffee roasters", coffee)
        commit_import(conn, preview_import(conn, card, "s.csv", FIXTURE.read_bytes()),
                      archive=tmp_path_factory.mktemp("a"))
    return committed_database_url


def run(page, db, monkeypatch):
    monkeypatch.setenv("BUDGET_DATABASE_URL", db)
    return AppTest.from_file(str(PAGES / page), default_timeout=10).run()


def test_review_lists_only_uncategorized(db, monkeypatch):
    app = run("review_page.py", db, monkeypatch)
    assert not app.exception
    assert "3 uncategorized" in app.caption[0].value   # 5 imported, 2 coffees ruled


def test_rules_page_shows_rule_and_usage(db, monkeypatch):
    app = run("rules_page.py", db, monkeypatch)
    assert not app.exception
    labels = [b.label for b in app.button]
    assert "Save changes and re-run rules" in labels   # only shown when rules exist
    assert "Re-run all rules" in labels


def test_rerun_button(db, monkeypatch):
    app = run("rules_page.py", db, monkeypatch)
    next(b for b in app.button if b.label == "Re-run all rules").click().run()
    assert "Rules re-run: 0 newly categorized" in app.success[0].value
