"""Category lookups shared by the app pages."""

import psycopg

SEPARATOR = " › "


def subcategory_choices(conn: psycopg.Connection, include_inactive: bool = False,
                        kind: str | None = None) -> dict[int, str]:
    """Subcategory id -> "Top-level › Subcategory", in display order.

    kind = "expense" or "income" limits the list to that kind of category.
    """
    rows = conn.execute(
        f"""
        SELECT s.id, t.name || '{SEPARATOR}' || s.name
          FROM categories s
          JOIN categories t ON t.id = s.parent_id
         WHERE (%(inactive)s OR (s.active AND t.active))
           AND (%(kind)s::text IS NULL OR s.kind = %(kind)s)
         ORDER BY t.kind, t.sort_order, t.name, s.sort_order, s.name
        """,
        {"inactive": include_inactive, "kind": kind},
    ).fetchall()
    return dict(rows)
