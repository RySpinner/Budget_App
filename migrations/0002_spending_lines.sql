-- 0002_spending_lines.sql
-- Reporting building blocks (SPEC section 7). Nothing here is stored;
-- everything is computed when queried.

------------------------------------------------------------------------
-- spending_lines: one row per thing that counts as spending.
--   * a transaction WITHOUT splits -> one line, using its own category
--   * a transaction WITH splits    -> one line per split, using the split's
--                                     category and amount
-- All reports and the Transactions page read from this.
------------------------------------------------------------------------
CREATE VIEW spending_lines AS
WITH lines AS (
    SELECT t.id          AS transaction_id,
           NULL::integer AS split_id,
           t.amount_cents,
           t.category_id,
           t.note
      FROM transactions t
     WHERE NOT EXISTS (SELECT 1 FROM splits s WHERE s.transaction_id = t.id)
    UNION ALL
    SELECT s.transaction_id,
           s.id,
           s.amount_cents,
           s.category_id,
           s.note
      FROM splits s
)
SELECT l.transaction_id,
       l.split_id,
       (SELECT count(*) FROM splits s WHERE s.transaction_id = t.id) AS split_count,
       t.source,
       t.effective_date,
       date_trunc('month', t.effective_date)::date AS month,
       l.amount_cents,
       l.category_id,
       sub.name      AS subcategory,
       top.id        AS top_category_id,
       top.name      AS top_category,
       t.merchant,
       t.raw_description,
       t.card_id,
       c.display_name AS card,
       t.purchaser_id,
       p.name        AS purchaser,
       l.note
  FROM lines l
  JOIN transactions t      ON t.id = l.transaction_id
  JOIN people p            ON p.id = t.purchaser_id
  LEFT JOIN cards c        ON c.id = t.card_id
  LEFT JOIN categories sub ON sub.id = l.category_id
  LEFT JOIN categories top ON top.id = sub.parent_id;

------------------------------------------------------------------------
-- Budgets. The budget for a month is the row with the latest
-- effective_from on or before that month (changing a budget never
-- rewrites history). NULL when no budget is set.
------------------------------------------------------------------------
CREATE FUNCTION budget_for_month(top_category_id INTEGER, day DATE) RETURNS BIGINT AS $$
    SELECT monthly_amount_cents
      FROM budgets
     WHERE category_id = top_category_id
       AND effective_from <= date_trunc('month', day)::date
     ORDER BY effective_from DESC
     LIMIT 1;
$$ LANGUAGE sql STABLE;

-- Prorated budget on a given day:
--   monthly budget x (day of month / days in month), rounded to the cent.
-- Uses NUMERIC (exact decimal), never floating point.
CREATE FUNCTION budget_to_date(top_category_id INTEGER, day DATE) RETURNS BIGINT AS $$
    SELECT round(
               budget_for_month(top_category_id, day)::numeric
               * EXTRACT(DAY FROM day)
               / EXTRACT(DAY FROM (date_trunc('month', day) + interval '1 month - 1 day'))
           )::bigint;
$$ LANGUAGE sql STABLE;
