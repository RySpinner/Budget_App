"""Categorization rules (SPEC section 6).

A rule says "if the bank description matches PATTERN (and optionally the
account / amount range fit), then ..." one of:
  - put the transaction in CATEGORY (which makes it expense or income), or
  - mark it as a TRANSFER between our own accounts, optionally naming the
    account on the other side (the counterpart). The counterpart is a hint
    for transfer matching (phase 10); a rule never links two rows itself.
Rules are tried in priority order (lowest number first, then oldest first);
the first match wins.

What rules may touch:
  - Never a row a person categorized or classified by hand ('manual'), nor a
    row that is already linked into a transfer. The database refuses manual
    overwrites too (see migrations/0004_accounts.sql).
  - A row the parser called a transfer (a card payment) only gets transfer
    rules, which can add the counterpart; category rules skip it.
  - Otherwise a rule's kind wins over the parser's (as with categories).

Re-running rules after editing them (apply_rules) brings rule-set rows up to
date: a row moves to the new first-matching rule, or back to the Review
queue if no rule matches it any more. Merchant names set by a rule are left
as they are in that case.
"""

import re
from dataclasses import dataclass

import psycopg

from budget.transactions import mark_as_transfer

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
    category_id: int | None            # None for a transfer rule
    merchant_name: str | None
    kind: str = "expense"              # the kind a match gives the row: expense, income (from the category) or transfer
    counterpart_account_id: int | None = None   # transfer rules: the account on the other side, if known

    @property
    def is_transfer(self) -> bool:
        return self.kind == "transfer"

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


def validate_rule(match_type: str, pattern: str, account_id: int | None = None,
                  counterpart_account_id: int | None = None) -> None:
    """Raise ValueError if a rule couldn't work. Used before saving rules."""
    if counterpart_account_id is not None and counterpart_account_id == account_id:
        raise ValueError("A transfer's other account can't be the account the rule applies to")
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
               r.category_id, r.merchant_name,
               CASE WHEN r.is_transfer THEN 'transfer' ELSE c.kind END,
               r.counterpart_account_id
          FROM rules r LEFT JOIN categories c ON c.id = r.category_id
         WHERE r.active
         ORDER BY r.priority, r.id
        """
    ).fetchall()
    return [Rule(*row) for row in rows]


def rule_may_apply(rule: Rule, kind: str | None, kind_source: str | None) -> bool:
    """Whether a rule is allowed to touch a row of this kind (see the module docstring)."""
    if kind_source == "manual" and kind is not None and rule.kind != kind:
        return False    # never change a kind a person chose
    if kind == "transfer" and kind_source == "parser" and not rule.is_transfer:
        return False    # the bank says it's a transfer (e.g. a card payment): no category rules
    return True


def first_match(rules: list[Rule], raw_description: str, account_id: int | None, amount_cents: int,
                kind: str | None = None, kind_source: str | None = None) -> Rule | None:
    """The first rule (in priority order) that matches and is allowed to apply.

    kind / kind_source describe the row as it is now: on import, the parser's
    kind (source "parser"); when re-running rules, what is stored.
    """
    for rule in rules:
        if rule_may_apply(rule, kind, kind_source) and rule.matches(raw_description, account_id, amount_cents):
            return rule
    return None


@dataclass
class ApplyResult:
    categorized: int = 0     # rows no rule covered before that a rule now covers (category or transfer)
    changed: int = 0         # rule-set rows moved to a different rule/category
    uncategorized: int = 0   # rule-set rows that no rule matches any more (back to Review)


def apply_rules(conn: psycopg.Connection, transaction_ids: list[int] | None = None) -> ApplyResult:
    """Run the active rules over existing transactions (all, or just the given ids).

    Skipped: manual categories, rows linked into a transfer, and split rows.
    A category rule sets the row's category and, through it, the kind; a
    transfer rule makes the row a transfer (no category). If a row that a
    rule had set no longer matches any rule, it goes back to Review: the
    rule's category is removed, and a kind that only the rule had given it is
    cleared (on a credit card it goes back to "expense", the parser default).
    Runs in one database transaction.
    """
    rules = load_rules(conn)
    result = ApplyResult()
    sql = """
        SELECT t.id, t.raw_description, t.account_id, t.amount_cents, t.category_id, t.category_source,
               t.rule_id, t.kind, t.kind_source, t.merchant
          FROM transactions t
         WHERE t.category_source IS DISTINCT FROM 'manual'
           AND t.transfer_id IS NULL
           AND NOT EXISTS (SELECT 1 FROM splits s WHERE s.transaction_id = t.id)
    """
    params: tuple = ()
    if transaction_ids is not None:
        sql += " AND t.id = ANY(%s)"
        params = (transaction_ids,)

    with conn.transaction():
        rows = conn.execute(sql, params).fetchall()
        for txn_id, raw, account_id, amount, category_id, category_source, rule_id, kind, kind_source, merchant in rows:
            rule = first_match(rules, raw, account_id, amount, kind, kind_source)
            set_by_rule = rule_id is not None or category_source == "rule" or kind_source == "rule"

            if rule is None:
                if set_by_rule:
                    _release_from_rule(conn, txn_id)
                    result.uncategorized += 1
                continue

            new_kind_source = kind_source if kind == rule.kind else "rule"
            new_merchant = rule.merchant_name or merchant
            if (category_id, rule_id, kind, kind_source, merchant) == (
                    rule.category_id, rule.id, rule.kind, new_kind_source, new_merchant):
                continue  # already up to date
            conn.execute(
                """
                UPDATE transactions
                   SET category_id = %s, category_source = %s, rule_id = %s,
                       kind = %s, kind_source = %s, merchant = %s
                 WHERE id = %s AND category_source IS DISTINCT FROM 'manual'
                """,
                (rule.category_id, "rule" if rule.category_id is not None else None, rule.id,
                 rule.kind, new_kind_source, new_merchant, txn_id),
            )
            if not set_by_rule:
                result.categorized += 1
            elif (category_id, rule_id, kind) != (rule.category_id, rule.id, rule.kind):
                result.changed += 1
    return result


def _release_from_rule(conn: psycopg.Connection, txn_id: int) -> None:
    """Undo what rules did to a row: back to Review. Manual and parser choices stay."""
    conn.execute(
        """
        UPDATE transactions t
           SET category_id     = CASE WHEN t.category_source = 'rule' THEN NULL ELSE t.category_id END,
               category_source = CASE WHEN t.category_source = 'rule' THEN NULL ELSE t.category_source END,
               rule_id         = NULL,
               kind            = CASE WHEN t.kind_source <> 'rule' THEN t.kind
                                      WHEN a.account_class = 'liability' THEN 'expense' END,
               kind_source     = CASE WHEN t.kind_source <> 'rule' THEN t.kind_source
                                      WHEN a.account_class = 'liability' THEN 'parser' END
          FROM accounts a
         WHERE a.id = t.account_id AND t.id = %s
        """,
        (txn_id,),
    )


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
    category_id: int | None,
    match_type: str = "contains",
    priority: int = 100,
    merchant_name: str | None = None,
    account_id: int | None = None,
    min_amount_cents: int | None = None,
    max_amount_cents: int | None = None,
    created_from_transaction_id: int | None = None,
    *,
    is_transfer: bool = False,
    counterpart_account_id: int | None = None,
) -> int:
    """Save a rule. Give a category_id, or is_transfer=True (and optionally the counterpart account)."""
    validate_rule(match_type, pattern, account_id, counterpart_account_id)
    if is_transfer == (category_id is not None):
        raise ValueError("A rule sets either a category or 'transfer', not both or neither")
    return conn.execute(
        """
        INSERT INTO rules (priority, match_type, pattern, account_id, min_amount_cents, max_amount_cents,
                           category_id, is_transfer, counterpart_account_id, merchant_name,
                           created_from_transaction_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (priority, match_type, pattern.strip(), account_id, min_amount_cents, max_amount_cents,
         category_id, is_transfer, counterpart_account_id, merchant_name or None, created_from_transaction_id),
    ).fetchone()[0]


def rerun_for_review_queue(conn: psycopg.Connection) -> ApplyResult:
    """Run rules over the rows still waiting in Review (no category, not a transfer, not split)."""
    ids = [
        r[0] for r in conn.execute(
            """SELECT id FROM transactions t WHERE category_id IS NULL
               AND kind IS DISTINCT FROM 'transfer'
               AND NOT EXISTS (SELECT 1 FROM splits s WHERE s.transaction_id = t.id)"""
        )
    ]
    return apply_rules(conn, ids) if ids else ApplyResult()


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
        return rule_id, rerun_for_review_queue(conn).categorized


def mark_transfer_manually(
    conn: psycopg.Connection,
    transaction_id: int,
    save_rule_pattern: str | None = None,
    counterpart_account_id: int | None = None,
    rule_account_id: int | None = None,
) -> tuple[int | None, int]:
    """Mark one row as a transfer by hand, optionally saving a transfer rule from it.

    The saved rule applies to rule_account_id only (usually the row's own
    account) and names counterpart_account_id as the other side, if given.
    Returns (new_rule_id or None, number of OTHER rows in the Review queue
    the new rule marked as transfers).
    """
    with conn.transaction():
        mark_as_transfer(conn, transaction_id)
        if not save_rule_pattern:
            return None, 0
        rule_id = create_rule(
            conn, save_rule_pattern, None, account_id=rule_account_id,
            created_from_transaction_id=transaction_id,
            is_transfer=True, counterpart_account_id=counterpart_account_id,
        )
        return rule_id, rerun_for_review_queue(conn).categorized


def main() -> None:
    """python -m budget.rules : re-run all active rules over existing transactions."""
    from budget.db import connect

    with connect() as conn:
        r = apply_rules(conn)
    print(f"Newly categorized: {r.categorized}, changed: {r.changed}, back to review: {r.uncategorized}")


if __name__ == "__main__":
    main()
