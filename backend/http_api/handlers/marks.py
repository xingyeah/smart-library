import logging
from typing import Any, Dict, List

from auto_summary_refresher import enqueue_book_summary_refresh
from book_marks import BatchMarksError, apply_batch_marks, parse_batch_items
from http_api.responses import send_json

logger = logging.getLogger("http_api.marks")

# Same rule as one book's meta call: a book the reader wants or likes gets its summary refreshed first
SUMMARY_WORTHY_STATUSES = ("want", "reading")


def handle_post_batch_meta(handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any]) -> None:
    """POST /api/books/batch_meta {items: [{id, reading_status?, is_favorite?}]}: sets each item's marks in one
    transaction (at most 200 books). Answers {updated, previous}; `previous` posted back as items undoes the batch.
    400 when the batch is malformed or names a missing book, and then nothing is written."""
    try:
        parsed = parse_batch_items(body.get("items"))
        previous = apply_batch_marks(parsed)
    except BatchMarksError as error:
        send_json(handler, {"error": str(error)}, 400)
        return
    for book_id, marks in parsed:
        if marks.get("is_favorite") or marks.get("reading_status") in SUMMARY_WORTHY_STATUSES:
            enqueue_book_summary_refresh(book_id)
    logger.info("batch marks set on %d books", len(parsed))
    send_json(handler, {"updated": len(parsed), "previous": previous})
