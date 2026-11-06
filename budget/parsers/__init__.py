"""Parsers, one module per institution. cards.parser_key picks one by name.

To add an issuer: create budget/parsers/<issuer>.py with a
`parse(text: str) -> list[ParsedRow]` function, register it below, and add
tests/test_parser_<issuer>.py with a fake sample CSV in tests/fixtures/<issuer>/.
"""

from collections.abc import Callable

from budget.parsers import amex, chase, citi, credit_union
from budget.parsers.base import ParsedRow

PARSERS: dict[str, Callable[[str], list[ParsedRow]]] = {
    "citi": citi.parse,
    "chase": chase.parse,
    "credit_union": credit_union.parse,
    "amex": amex.parse,
}


def get_parser(parser_key: str) -> Callable[[str], list[ParsedRow]]:
    try:
        return PARSERS[parser_key]
    except KeyError:
        raise ValueError(f"No parser named {parser_key!r}. Known parsers: {sorted(PARSERS)}") from None
