"""Import page tests using Streamlit's headless AppTest.

AppTest can't drive the file uploader, so uploading is covered by the
importer tests; here we check the page renders and the undo flow works.
"""

from pathlib import Path

import psycopg
import pytest
from streamlit.testing.v1 import AppTest

from budget.importer import commit_import, preview_import

PAGE = str(Path(__file__).resolve().parent.parent / "app" / "views" / "import_page.py")
FIXTURE = Path(__file__).parent / "fixtures" / "citi" / "sample.csv"


@pytest.fixture(scope="module")
def batch_id(committed_database_url, tmp_path_factory):
    with psycopg.connect(committed_database_url) as conn:
        person = conn.execute("INSERT INTO people (name) VALUES ('Page Person') RETURNING id").fetchone()[0]
        card = conn.execute(
            "INSERT INTO cards (display_name, issuer, last4, owner_person_id, parser_key) "
            "VALUES ('Page Citi', 'Citi', '0000', %s, 'citi') RETURNING id",
            (person,),
        ).fetchone()[0]
        preview = preview_import(conn, card, "sample.csv", FIXTURE.read_bytes())
        return commit_import(conn, preview, archive=tmp_path_factory.mktemp("archive"))


@pytest.fixture
def app(committed_database_url, batch_id, monkeypatch):
    monkeypatch.setenv("BUDGET_DATABASE_URL", committed_database_url)
    return AppTest.from_file(PAGE, default_timeout=10).run()


def test_page_renders_with_past_imports(app):
    assert not app.exception
    assert app.title[0].value == "Import"
    batches = app.dataframe[0].value
    assert list(batches["Batch"]) == [1]
    assert list(batches["Status"]) == ["Active"]


def test_undo_needs_confirmation(app, committed_database_url, batch_id):
    app.selectbox[0].select(batch_id).run()
    undo = next(b for b in app.button if b.label == "Undo…")
    undo.click().run()
    assert "Undo batch" in app.warning[0].value

    cancel = next(b for b in app.button if b.label == "Cancel")
    cancel.click().run()
    assert not app.warning
    with psycopg.connect(committed_database_url) as conn:
        assert conn.execute("SELECT count(*) FROM transactions").fetchone()[0] == 5

    next(b for b in app.button if b.label == "Undo…").click().run()
    next(b for b in app.button if b.label == "Yes, undo it").click().run()
    assert "undone; 5 transactions deleted" in app.success[0].value
    with psycopg.connect(committed_database_url) as conn:
        assert conn.execute("SELECT count(*) FROM transactions").fetchone()[0] == 0
    assert list(app.dataframe[0].value["Status"])[0].startswith("Undone")
