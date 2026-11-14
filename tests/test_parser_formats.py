"""Every parser reads its own institution's files and refuses everyone else's.

Fixtures live in tests/fixtures/<parser_key>/*.csv, so a new parser and its
fixtures are covered here automatically. This matters for the Import page:
it picks an account by finding the one parser that can read the file.
"""

from pathlib import Path

import pytest

from budget.parsers import PARSERS

FIXTURES = Path(__file__).parent / "fixtures"
FILES = sorted(FIXTURES.glob("*/*.csv"))


def test_every_parser_has_fixtures():
    assert set(PARSERS) <= {p.parent.name for p in FILES}


@pytest.mark.parametrize("path", FILES, ids=lambda p: f"{p.parent.name}/{p.name}")
@pytest.mark.parametrize("key", sorted(PARSERS))
def test_parser_accepts_only_its_own_files(key, path):
    text = path.read_text(encoding="utf-8-sig")
    if path.parent.name == key:
        assert PARSERS[key](text), f"{key} found no rows in its own fixture {path.name}"
    else:
        with pytest.raises(ValueError):
            PARSERS[key](text)
