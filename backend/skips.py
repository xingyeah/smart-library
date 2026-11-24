"""Books the reader swiped away on the tonight page.

Every skip is kept: what a reader turns down says as much about their taste as what they pick. Only a skip from
the tonight page (not from search results, where it only means "not this query") hides the book for a while.
"""

from typing import Any, Set

from database import get_conn, write_tx

SKIP_CONTEXT_TONIGHT = "tonight"
SKIP_CONTEXT_SEARCH = "search"
SKIP_CONTEXTS = (SKIP_CONTEXT_TONIGHT, SKIP_CONTEXT_SEARCH)
TONIGHT_HIDE_DAYS = 30
MAX_QUERY_CHARS = 200


class SkipError(ValueError):
    """The skip cannot be recorded: unknown context or book."""


def record_skip(book_id: int, context: str, query: str = "") -> None:
    """Keeps one skip of `book_id`. `query` is the search sentence for a skip from search results."""
    if context not in SKIP_CONTEXTS:
        raise SkipError(f"unknown skip context {context!r}")
    with write_tx() as conn:
        if not conn.execute("SELECT 1 FROM books WHERE id = ? AND is_deleted = 0", (book_id,)).fetchone():
            raise SkipError(f"book {book_id} does not exist")
        conn.execute(
            "INSERT INTO book_skips (book_id, context, query) VALUES (?, ?, ?)",
            (book_id, context, query.strip()[:MAX_QUERY_CHARS] if context == SKIP_CONTEXT_SEARCH else ""),
        )


def undo_skip(book_id: int) -> bool:
    """Deletes the latest skip of `book_id`; False when there is none."""
    with write_tx() as conn:
        row = conn.execute("SELECT MAX(id) FROM book_skips WHERE book_id = ?", (book_id,)).fetchone()
        if row[0] is None:
            return False
        conn.execute("DELETE FROM book_skips WHERE id = ?", (row[0],))
        return True


def recently_skipped_ids(conn: Any = None, days: int = TONIGHT_HIDE_DAYS) -> Set[int]:
    """Ids of the books skipped from the tonight page within the last `days` days."""
    own = conn is None
    conn = conn or get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT book_id FROM book_skips WHERE context = ? AND created_at >= datetime('now', ?)",
            (SKIP_CONTEXT_TONIGHT, f"-{int(days)} days"),
        ).fetchall()
        return {int(row[0]) for row in rows}
    finally:
        if own:
            conn.close()
