"""The reader's own marks on a book (reading status, favourite, rating), and setting them on many books at once."""

from typing import Any, Dict, List, Tuple

from database import write_tx

UNREAD = "unread"
READING_STATUSES = (UNREAD, "want", "reading", "read")
RATING_MIN = 0
MAX_BATCH_BOOKS = 200


class BatchMarksError(ValueError):
    """The batch is malformed or names a book that is missing; nothing was written."""


def is_new_mark(row: Any, marks: Dict[str, Any]) -> bool:
    """Whether `marks` gives the book a mark it does not have now. The same value sent again is none, and neither
    is taking a mark away: marked_at is when the reader last marked the book, not when they last touched it."""
    unmarked = {"reading_status": UNREAD, "is_favorite": 0, "user_rating": RATING_MIN}
    return any(value != unmarked[column] and value != (row[column] or unmarked[column])
               for column, value in marks.items())


def _item_marks(item: Any) -> Tuple[int, Dict[str, Any]]:
    if not isinstance(item, dict):
        raise BatchMarksError("each item must be an object")
    book_id = item.get("id")
    if not isinstance(book_id, int) or isinstance(book_id, bool) or book_id <= 0:
        raise BatchMarksError(f"item id must be a positive integer, got {book_id!r}")
    marks: Dict[str, Any] = {}
    if "reading_status" in item:
        if item["reading_status"] not in READING_STATUSES:
            raise BatchMarksError(f"book {book_id}: reading_status must be one of {', '.join(READING_STATUSES)}")
        marks["reading_status"] = item["reading_status"]
    if "is_favorite" in item:
        marks["is_favorite"] = 1 if item["is_favorite"] else 0
    if not marks:
        raise BatchMarksError(f"book {book_id}: give reading_status or is_favorite")
    return book_id, marks


def parse_batch_items(items: Any) -> List[Tuple[int, Dict[str, Any]]]:
    """[(book_id, {column: value})] from the posted items. Each book at most once, so the previous state handed
    back for undo is unambiguous."""
    if not isinstance(items, list) or not items:
        raise BatchMarksError("items must be a non-empty list")
    if len(items) > MAX_BATCH_BOOKS:
        raise BatchMarksError(f"at most {MAX_BATCH_BOOKS} books per batch, got {len(items)}")
    parsed = [_item_marks(item) for item in items]
    ids = [book_id for book_id, _ in parsed]
    if len(set(ids)) != len(ids):
        raise BatchMarksError("a book appears twice in the batch")
    return parsed


def apply_batch_marks(parsed: List[Tuple[int, Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Writes every item's marks in one transaction, all or none. Returns each book's marks from before, in the
    items' order, which posted back as a batch undoes it. BatchMarksError when a book is missing or deleted."""
    ids = [book_id for book_id, _ in parsed]
    with write_tx() as conn:
        rows = {row["id"]: row for row in conn.execute(
            f"SELECT id, reading_status, is_favorite, user_rating FROM books WHERE is_deleted = 0 "
            f"AND id IN ({','.join('?' for _ in ids)})", ids)}
        missing = [book_id for book_id in ids if book_id not in rows]
        if missing:
            raise BatchMarksError(f"books not found: {missing[:10]}")
        for book_id, marks in parsed:
            sets = [f"{column} = ?" for column in marks]
            if is_new_mark(rows[book_id], marks):
                sets.append("marked_at = CURRENT_TIMESTAMP")
            conn.execute(f"UPDATE books SET {', '.join(sets)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         [*marks.values(), book_id])
    return [{"id": book_id, "reading_status": rows[book_id]["reading_status"] or UNREAD,
             "is_favorite": bool(rows[book_id]["is_favorite"])} for book_id in ids]
