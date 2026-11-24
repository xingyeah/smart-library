import logging
from typing import Any, Dict, List

from database import get_conn
from http_api.responses import send_json
from search.shelf import attach_ai_meta, format_book_row
from skips import SkipError, record_skip, undo_skip
from tonight import continue_reading_row, other_reading_count, tonight_picks

logger = logging.getLogger("http_api.tonight")

MAX_SEEN_IDS = 500


def _seen_ids(raw: str) -> List[int]:
    """The book ids of a comma-separated `seen` parameter; anything that is not a number is left out."""
    ids = [int(part) for part in raw.split(",") if part.strip().isdigit()]
    return ids[:MAX_SEEN_IDS]


def handle_get_tonight(handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any]) -> None:
    """GET /api/tonight[?seen=1,2,3]: {continue, other_reading, picks}. `seen` lists the picks already shown, so
    'another batch' brings new books; `continue` and `other_reading` are only worth asking for on the first batch."""
    seen = _seen_ids((qs.get("seen") or [""])[0])
    conn = get_conn()
    try:
        row = continue_reading_row(conn)
        current = attach_ai_meta([format_book_row(row)], conn)[0] if row else None
        other = other_reading_count(conn, current is not None)
    finally:
        conn.close()
    excluded = seen + ([int(current["id"])] if current else [])
    send_json(handler, {"continue": current, "other_reading": other, "picks": tonight_picks(excluded)})


def _body_book_id(body: Dict[str, Any]) -> int:
    book_id = body.get("book_id")
    if not isinstance(book_id, int) or isinstance(book_id, bool):
        raise SkipError("book_id must be an integer")
    return book_id


def handle_post_skip(handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any]) -> None:
    """POST /api/skips: {book_id, context: 'tonight' | 'search', query?}. Keeps the skip."""
    try:
        record_skip(_body_book_id(body), str(body.get("context") or ""), str(body.get("query") or ""))
    except SkipError as error:
        send_json(handler, {"error": str(error)}, 400)
        return
    send_json(handler, {"ok": True})


def handle_post_skip_undo(handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any]) -> None:
    """POST /api/skips/undo: {book_id}. Takes back the latest skip of the book."""
    try:
        send_json(handler, {"ok": undo_skip(_body_book_id(body))})
    except SkipError as error:
        send_json(handler, {"error": str(error)}, 400)
