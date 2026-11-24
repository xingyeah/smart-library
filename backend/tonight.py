"""The tonight page: the book to carry on with and a batch of picks that leaves out what was skipped."""

from typing import Any, Dict, Iterable, List, Optional

from search.interest import recommend_for_tonight
from skips import recently_skipped_ids

TONIGHT_PICKS = 10


def continue_reading_row(conn: Any) -> Optional[Dict[str, Any]]:
    """The book opened most recently that is marked as being read and not finished; None when there is none."""
    row = conn.execute(
        "SELECT * FROM books WHERE is_deleted = 0 AND reading_status = 'reading' AND last_opened_at IS NOT NULL "
        "AND COALESCE(read_progress, 0) < 1 ORDER BY last_opened_at DESC, id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def other_reading_count(conn: Any, has_current: bool) -> int:
    """How many books are marked as being read besides the one on top (`has_current`)."""
    total = conn.execute(
        "SELECT COUNT(*) FROM books WHERE is_deleted = 0 AND reading_status = 'reading'"
    ).fetchone()[0]
    return max(0, int(total) - (1 if has_current else 0))


def tonight_picks(seen_ids: Iterable[int]) -> List[Dict[str, Any]]:
    """Up to TONIGHT_PICKS books, none of them in `seen_ids` or skipped from this page in the last 30 days."""
    excluded = set(seen_ids) | recently_skipped_ids()
    items = recommend_for_tonight(TONIGHT_PICKS, excluded)["items"]
    return [{**book, "tonight_reason": book.get("reason_text") or ""} for book in items]
