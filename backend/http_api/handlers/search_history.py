from typing import Any, Dict, List

from http_api.responses import send_json
from search_history import SearchHistoryError, delete_search, recent_searches, record_search


def handle_get_search_history(handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any]) -> None:
    """GET /api/search_history: {items: [{query, used_at}]}, newest first."""
    send_json(handler, {"items": recent_searches()})


def handle_post_search_history(handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any]) -> None:
    """POST /api/search_history: {query}. Moves the query to the front of the history."""
    try:
        record_search(body.get("query"))
    except SearchHistoryError as error:
        send_json(handler, {"error": str(error)}, 400)
        return
    send_json(handler, {"ok": True})


def handle_post_search_history_delete(handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any]) -> None:
    """POST /api/search_history/delete: {query}. Removes that one query."""
    try:
        send_json(handler, {"ok": delete_search(body.get("query"))})
    except SearchHistoryError as error:
        send_json(handler, {"error": str(error)}, 400)
