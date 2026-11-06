"""Categorization rules (SPEC section 6).

A rule says "if the bank description matches PATTERN (and optionally the
account / amount range fit), put the transaction in CATEGORY". Rules are tried
in priority order (lowest number first, then oldest first); the first match
wins.

Rules only ever touch rows whose category_source is NULL (uncategorized) or
'rule'. Rows a person categorized by hand ('manual') are never changed, and
the database refuses it too (see migrations/0001_core_schema.sql).

Re-running rules after editing them (apply_rules) brings rule-set rows up to
date: a row moves to the new first-matching rule, or back to the Review
queue if no rule matches it any more. Merchant names set by a rule are left
as they are in that case.
"""

import re
from dataclasses import dataclass

import psycopg

MATCH_TYPES = ("contains", "exact", "regex")


@dataclass(frozen=True)
class Rule:
    id: int
    priority: int
    match_type: str
    pattern: str
    account_id: int | None
    min_amount_cents: int | None
    max_amount_cents: int | None
    category_id: int
    merchant_name: str | None
    category_kind: str = "expense"     # "expense" or "income": the kind a match gives the row

    def matches(self, raw_description: str, account_id: int | None, amount_cents: int) -> bool:
        if self.account_id is not None and self.account_id != account_id:
            return False
        if self.min_amount_cents is not None and amount_cents < self.min_amount_cents:
            return False
        if self.max_amount_cents is not None and amount_cents > self.max_amount_cents:
            return False

        # contains / exact: ignore case and treat runs of spaces as one space.
        text = " ".join(raw_description.split()).casefold()
        pattern = " ".join(self.pattern.split()).casefold()
        if self.match_type == "contains":
            return pattern in text
        if self.match_type == "exact":
            return pattern == text
        if self.match_type == "regex":
            return re.search(self.pattern, raw_description, re.IGNORECASE) is not None
        raise ValueError(f"Unknown match_type {self.match_type!r}")


def validate_rule(match_type: str, pattern: str) -> None:
    """Raise ValueError if a rule couldn't work. Used before saving rules."""
    if match_type not in MATCH_TYPES:
        raise ValueError(f"match_type must be one of {MATCH_TYPES}")
    if not pattern or not pattern.strip():
        raise ValueError("Pattern can't be empty")
    if match_type == "regex":
        try:
            re.compile(pattern)
        except re.error as e:
            raise ValueError(f"Invalid regular expression {pattern!r}: {e}") from None


def load_rules(conn: psycopg.Connection) -> list[Rule]:
    rows = conn.execute(
        """
        SELECT r.id, r.priority, r.match_type, r.pattern, r.account_id, r.min_amount_cents, r.max_amount_cents,
               r.category_id, r.merchant_name, c.kind
          FROM rules r JOIN categories c ON c.id = r.category_id
         WHERE r.active
         ORDER BY r.priority, r.id
        """
    ).fetchall()
    return [Rule(*row) for row in rows]


def first_match(rules: list[Rule], raw_description: str, account_id: int | None, amount_cents: int,
                kind: str | None = None, kind_is_manual: bool = False) -> Rule | None:
    """The first rule (in priority order) that matches and is allowed to apply.

    Rules never apply to transfers, and never change a kind a person chose
    (a manual "expense" row can't get an income category from a rule).
    """
    if kind == "transfer":
        return None
    for rule in rules:
        if kind_is_manual and kind is not None and rule.category_kind != kind:
            continue
        if rule.matches(raw_description, account_id, amount_cents):
            return rule
    return None


@dataclass
class ApplyResult:
    categorized: int = 0     # uncategorized rows that a rule now covers
    changed: int = 0         # rule-set rows moved to a different rule/category
    uncategorized: int = 0   # rule-set rows that no rule matches any more


def apply_rules(conn: psycopg.Connection, transaction_ids: list[int] | None = None) -> ApplyResult:
    """Run the active rules over existing transactions (all, or just the given ids).

    Manual categories and transfers are skipped. A rule sets the row's
    category and, through the category, its kind (expense or income). If a
    rule-set row no longer matches any rule, it goes back to Review, and a
    kind that only a rule had given it is cleared too. Runs in one database
    transaction.
    """
    rules = load_rules(conn)
    result = ApplyResult()
    sql = """
        SELECT id, raw_description, account_id, amount_cents, category_id, rule_id, kind, kind_source
          FROM transactions
         WHERE category_source IS DISTINCT FROM 'manual'
           AND kind IS DISTINCT FROM 'transfer'
    """
    params: tuple = ()
    if transaction_ids is not None:
        sql += " AND id = ANY(%s)"
        params = (transaction_ids,)

    with conn.transaction():
        rows = conn.execute(sql, params).fetchall()
        for txn_id, raw, account_id, amount, category_id, rule_id, kind, kind_source in rows:
            rule = first_match(rules, raw, account_id, amount, kind, kind_source == "manual")
            if rule is None:
                if category_id is not None:
                    conn.execute(
                        """UPDATE transactions
                              SET category_id = NULL, category_source = NULL, rule_id = NULL,
                                  kind        = CASE WHEN kind_source = 'rule' THEN NULL ELSE kind END,
                                  kind_source = CASE WHEN kind_source = 'rule' THEN NULL ELSE kind_source END
                            WHERE id = %s AND category_source = 'rule'""",
                        (txn_id,),
                    )
                    result.uncategorized += 1
                continue

            if category_id == rule.category_id and rule_id == rule.id and rule.merchant_name is None:
                continue  # already up to date
            # The kind follows the category; keep the existing source if the kind doesn't change.
            new_kind_source = kind_source if kind == rule.category_kind else "rule"
            conn.execute(
                """
                UPDATE transactions
                   SET category_id = %s, category_source = 'rule', rule_id = %s,
                       kind = %s, kind_source = %s,
                       merchant = COALESCE(%s, merchant)
                 WHERE id = %s AND category_source IS DISTINCT FROM 'manual'
                """,
                (rule.category_id, rule.id, rule.category_kind, new_kind_source, rule.merchant_name, txn_id),
            )
            if category_id is None:
                result.categorized += 1
            elif category_id != rule.category_id or rule_id != rule.id:
                result.changed += 1
    return result


def suggest_pattern(merchant: str | None, raw_description: str) -> str:
    """A 'contains' pattern for a new rule, based on the cleaned merchant name.

    Uses the longest leading run of the merchant's words that appears in the
    bank description, so the rule actually matches it. E.g. merchant
    "FAKE PIZZA SPRINGFIELD" from "TST* FAKE PIZZA #0123 SPRINGFIELD IL" gives
    "FAKE PIZZA".
    """
    words = (merchant or "").split()
    text = " ".join(raw_description.split()).casefold()
    for n in range(len(words), 0, -1):
        candidate = " ".join(words[:n])
        if candidate.casefold() in text:
            return candidate
    return " ".join(raw_description.split())


def create_rule(
    conn: psycopg.Connection,
    pattern: str,
    category_id: int,
    match_type: str = "contains",
    priority: int = 100,
    merchant_name: str | None = None,
    account_id: int | None = None,
    min_amount_cents: int | None = None,
    max_amount_cents: int | None = None,
    created_from_transaction_id: int | None = None,
) -> int:
    validate_rule(match_type, pattern)
    return conn.execute(
        """
        INSERT INTO rules (priority, match_type, pattern, account_id, min_amount_cents, max_amount_cents,
                           category_id, merchant_name, created_from_transaction_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (priority, match_type, pattern.strip(), account_id, min_amount_cents, max_amount_cents,
         category_id, merchant_name or None, created_from_transaction_id),
    ).fetchone()[0]


def categorize_manually(
    conn: psycopg.Connection, transaction_id: int, category_id: int, save_rule_pattern: str | None = None
) -> tuple[int | None, int]:
    """Categorize one transaction by hand, optionally saving a rule from it.

    The category decides the kind (expense or income); both are marked
    'manual'. If the row was half of a transfer, that transfer is dissolved
    first (the other leg goes back to unmatched).

    Returns (new_rule_id or None, number of OTHER uncategorized rows the new
    rule categorized).
    """
    with conn.transaction():
        conn.execute(
            "DELETE FROM transfers WHERE id = (SELECT transfer_id FROM transactions WHERE id = %s)",
            (transaction_id,),
        )
        conn.execute(
            """UPDATE transactions
                  SET kind = (SELECT kind FROM categories WHERE id = %s), kind_source = 'manual',
                      category_id = %s, category_source = 'manual', rule_id = NULL
                WHERE id = %s""",
            (category_id, category_id, transaction_id),
        )
        if not save_rule_pattern:
            return None, 0

        rule_id = create_rule(
            conn, save_rule_pattern, category_id, created_from_transaction_id=transaction_id
        )
        uncategorized_ids = [
            r[0] for r in conn.execute(
                """SELECT id FROM transactions t WHERE category_id IS NULL
                   AND kind IS DISTINCT FROM 'transfer'
                   AND NOT EXISTS (SELECT 1 FROM splits s WHERE s.transaction_id = t.id)"""
            )
        ]
        result = apply_rules(conn, uncategorized_ids) if uncategorized_ids else ApplyResult()
        return rule_id, result.categorized


def main() -> None:
    """python -m budget.rules : re-run all active rules over existing transactions."""
    from budget.db import connect

    with connect() as conn:
        r = apply_rules(conn)
    print(f"Newly categorized: {r.categorized}, changed: {r.changed}, back to review: {r.uncategorized}")


if __name__ == "__main__":
    main()
