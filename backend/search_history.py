"""The sentences the reader searched for, offered back as suggestions under the search bar.

A repeated search keeps one row and moves to the front, so the list reads newest first without duplicates.
"""

import re
from typing import Any, Dict, List

from database import get_conn, write_tx

MAX_KEPT = 50
MAX_LISTED = 8
MAX_QUERY_CHARS = 200


class SearchHistoryError(ValueError):
    """The query is empty or not text."""


def normalize_query(raw: Any) -> str:
    """The query with runs of whitespace folded to one space, cut to MAX_QUERY_CHARS; raises when nothing is left."""
    if not isinstance(raw, str):
        raise SearchHistoryError("query must be a string")
    query = re.sub(r"\s+", " ", raw).strip()[:MAX_QUERY_CHARS]
    if not query:
        raise SearchHistoryError("query is empty")
    return query


def record_search(raw: Any) -> None:
    """Puts the query at the front of the history and drops the oldest rows beyond MAX_KEPT."""
    query = normalize_query(raw)
    with write_tx() as conn:
        # Delete then insert so the row gets the newest id, which is the order the history is listed in
        conn.execute("DELETE FROM search_history WHERE query = ?", (query,))
        conn.execute("INSERT INTO search_history (query) VALUES (?)", (query,))
        conn.execute(
            "DELETE FROM search_history WHERE id NOT IN (SELECT id FROM search_history ORDER BY id DESC LIMIT ?)",
            (MAX_KEPT,),
        )


def delete_search(raw: Any) -> bool:
    """Removes one query from the history; False when it was not there."""
    query = normalize_query(raw)
    with write_tx() as conn:
        return conn.execute("DELETE FROM search_history WHERE query = ?", (query,)).rowcount > 0


def recent_searches(limit: int = MAX_LISTED) -> List[Dict[str, str]]:
    """The newest `limit` searches, newest first."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT query, used_at FROM search_history ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [{"query": row["query"], "used_at": row["used_at"]} for row in rows]
    finally:
        conn.close()
