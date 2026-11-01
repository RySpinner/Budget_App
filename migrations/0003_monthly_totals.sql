-- 0003_monthly_totals.sql
-- Monthly totals by top-level category and subcategory (SPEC section 7),
-- built on spending_lines, so split lines count in their own categories.
-- Uncategorized spending appears with NULL category ids.

CREATE VIEW monthly_totals AS
SELECT month,
       top_category_id,
       category_id,
       sum(amount_cents)::bigint AS amount_cents,
       count(*)                  AS line_count
  FROM spending_lines
 GROUP BY month, top_category_id, category_id;
