import pytest

from budget.local_sync import get_stored, pull, push, status

FAKE = '[[person]]\nname = "Fake Person"\n'


def test_push_then_pull_to_a_second_machine(conn, tmp_path):
    laptop_a = tmp_path / "a" / "local.toml"
    laptop_a.parent.mkdir()
    laptop_a.write_text(FAKE, encoding="utf-8", newline="")
    assert push(conn, laptop_a) == "saved"

    laptop_b = tmp_path / "b" / "seed" / "local.toml"  # folder doesn't exist yet
    assert pull(conn, laptop_b) == "downloaded"
    assert laptop_b.read_bytes() == laptop_a.read_bytes()


def test_line_endings_survive(conn, tmp_path):
    f = tmp_path / "local.toml"
    f.write_bytes(b'[[person]]\r\nname = "Fake"\r\n')
    push(conn, f)
    out = tmp_path / "out.toml"
    pull(conn, out)
    assert out.read_bytes() == f.read_bytes()


def test_push_twice_is_unchanged_then_replaces(conn, tmp_path):
    f = tmp_path / "local.toml"
    f.write_text(FAKE)
    assert push(conn, f) == "saved"
    assert push(conn, f) == "unchanged"
    f.write_text(FAKE + '[[person]]\nname = "Other"\n')
    assert push(conn, f) == "replaced"
    assert "Other" in get_stored(conn)[0]


def test_invalid_toml_is_not_saved(conn, tmp_path):
    f = tmp_path / "local.toml"
    f.write_text(FAKE)
    push(conn, f)
    f.write_text("[[person\nname = ")
    with pytest.raises(ValueError, match="not valid TOML"):
        push(conn, f)
    assert get_stored(conn)[0] == FAKE


def test_pull_refuses_to_overwrite_a_different_file(conn, tmp_path):
    src = tmp_path / "src.toml"
    src.write_text(FAKE)
    push(conn, src)

    mine = tmp_path / "local.toml"
    mine.write_text('[[person]]\nname = "Mine"\n')
    with pytest.raises(FileExistsError):
        pull(conn, mine)
    assert "Mine" in mine.read_text()

    assert pull(conn, mine, force=True) == "replaced"
    assert mine.read_text() == FAKE
    assert "Mine" in (tmp_path / "local.toml.bak").read_text()


def test_pull_matching_file_is_unchanged(conn, tmp_path):
    f = tmp_path / "local.toml"
    f.write_text(FAKE)
    push(conn, f)
    assert pull(conn, f) == "unchanged"


def test_pull_with_nothing_stored(conn, tmp_path):
    with pytest.raises(LookupError):
        pull(conn, tmp_path / "local.toml")


def test_push_missing_file(conn, tmp_path):
    with pytest.raises(FileNotFoundError):
        push(conn, tmp_path / "nope.toml")


def test_status_messages(conn, tmp_path):
    f = tmp_path / "local.toml"
    assert status(conn, f).startswith("The database has no copy")
    f.write_text(FAKE)
    assert "Run `push`" in status(conn, f)
    push(conn, f)
    assert status(conn, f).startswith("In step")
    f.write_text(FAKE + "\n")
    assert status(conn, f).startswith("Different")
    f.unlink()
    assert status(conn, f).startswith("No local file")
