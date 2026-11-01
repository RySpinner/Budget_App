"""Category lookups shared by the app pages."""

import psycopg

SEPARATOR = " › "


def subcategory_choices(conn: psycopg.Connection, include_inactive: bool = False) -> dict[int, str]:
    """Subcategory id -> "Top-level › Subcategory", in display order."""
    rows = conn.execute(
        f"""
        SELECT s.id, t.name || '{SEPARATOR}' || s.name
          FROM categories s
          JOIN categories t ON t.id = s.parent_id
         WHERE %s OR (s.active AND t.active)
         ORDER BY t.sort_order, t.name, s.sort_order, s.name
        """,
        (include_inactive,),
    ).fetchall()
    return dict(rows)
