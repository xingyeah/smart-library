import json
import logging
import math
from typing import Any, Dict, List, Optional

import ai_search
from database import get_conn
from http_api.handlers.books import _qint, format_book_row
from http_api.responses import send_json
from search.interest import MAX_EXPLORATION_RATIO
from search_engine import (
    attach_ai_meta,
    interest_candidates,
    recommend_by_description,
    recommend_by_interests,
)

logger = logging.getLogger("http_api.recommend")


def handle_get_recommend_interests(handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any]) -> None:
    """GET /api/recommend/interests: Fast heuristic candidate books matching user interests."""
    q_str = lambda k, d="": (qs.get(k) or [d])[0]
    try:
        top_k = _qint(q_str("top_k", "10"), 10, minimum=1, maximum=100)
    except ValueError:
        send_json(handler, {"error": "top_k must be an integer"}, 400)
        return
    lang = q_str("language")
    send_json(handler, recommend_by_interests(top_k=top_k, language=lang))


def handle_post_recommend_describe(handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any]) -> None:
    """POST /api/recommend/describe: Natural language conceptual description search."""
    desc = body.get("description") or ""
    try:
        top_k = _qint(body.get("top_k"), 10, minimum=1, maximum=100)
    except ValueError:
        send_json(handler, {"error": "top_k must be an integer"}, 400)
        return
    lang = str(body.get("language") or "")
    res = recommend_by_description(
        desc, top_k=top_k, language=lang, cross_encoder=not body.get("ai")
    )
    send_json(handler, res)


def handle_post_search_ai_rerank(handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any]) -> None:
    """POST /api/search/ai_rerank: Cross-encoder reordering with LLM reasoning."""
    query = str(body.get("query") or "").strip()[:500]
    try:
        ids = [int(x) for x in (body.get("ids") or [])][:30]
    except (TypeError, ValueError):
        ids = []
    if not query or not ids:
        send_json(handler, {"ok": False, "reason": "empty"}, 400)
        return
    conn = get_conn()
    try:
        ph = ",".join("?" for _ in ids)
        rows = {
            int(r["id"]): r
            for r in conn.execute(f"SELECT * FROM books WHERE id IN ({ph})", ids)
        }
        items = attach_ai_meta([format_book_row(rows[i]) for i in ids if i in rows], conn)
    finally:
        conn.close()
    ranked, reason = ai_search.rerank(query, items, top_n=len(items))
    send_json(
        handler,
        {
            "ok": not reason,
            "reason": reason,
            "ids": [int(b["id"]) for b in ranked],
            "scores": {
                str(b["id"]): b.get("ai_score")
                for b in ranked
                if b.get("ai_score") is not None
            },
        },
    )


def _interest_config_error(body: Dict[str, Any]) -> Optional[str]:
    """Why the posted interest settings cannot be saved; None when they can."""
    excluded = body.get("excluded_categories")
    if excluded is not None and not (isinstance(excluded, list) and all(isinstance(name, str) for name in excluded)):
        return "excluded_categories must be a list of category names"
    ratio = body.get("exploration_ratio")
    is_number = isinstance(ratio, (int, float)) and not isinstance(ratio, bool)
    if ratio is not None and not (is_number and math.isfinite(ratio)):
        return f"exploration_ratio must be a number from 0 to {MAX_EXPLORATION_RATIO}"
    return None


def _interest_config_columns(body: Dict[str, Any]) -> Dict[str, Any]:
    """user_interest_config column -> value to store, for each setting the (already checked) body carries."""
    stored_as = {
        "interest_prompt": lambda prompt: str(prompt).strip(),
        "preferred_categories": lambda names: json.dumps(names, ensure_ascii=False),
        "excluded_categories": lambda names: json.dumps(
            sorted({name.strip() for name in names if name.strip()}), ensure_ascii=False),
        "exploration_ratio": lambda ratio: min(MAX_EXPLORATION_RATIO, max(0.0, float(ratio))),
        "schedule_enabled": lambda enabled: 1 if enabled else 0,
        "schedule_interval_hours": lambda hours: max(1, int(hours)),
    }
    return {column: store(body[column]) for column, store in stored_as.items() if body.get(column) is not None}


def handle_post_interests_config(handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any]) -> None:
    """POST /api/interests/config: Save the interest settings the body carries and return fresh recommendations;
    400 when excluded_categories or exploration_ratio has the wrong type."""
    error = _interest_config_error(body)
    if error:
        send_json(handler, {"error": error}, 400)
        return
    conn = get_conn()
    try:
        for column, value in _interest_config_columns(body).items():
            # `column` is one of the fixed names in _interest_config_columns, never text from the request
            conn.execute(
                f"UPDATE user_interest_config SET {column} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1", (value,))
        conn.commit()
    finally:
        conn.close()
    send_json(handler, recommend_by_interests(top_k=10))


def handle_post_interests_ai(handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any]) -> None:
    """POST /api/recommend/interests/ai: DeepSeek LLM picks 10 tailored books with explanations."""
    # Exploration picks are classics unrelated to the reader: among the candidates they would only crowd out
    # the books the model is asked to choose from
    res = interest_candidates(top_k=100, language=str(body.get("language") or ""))
    by_id = {int(b["id"]): b for b in res["items"]}
    picks, reason = ai_search.pick_books(
        ai_search.interest_profile(res.get("interest_prompt") or ""), res["items"]
    )
    items = []
    for rank, (bid, why) in enumerate(picks, 1):
        if bid in by_id:
            b = by_id[bid]
            b.update(rank=rank, recommend_type="ai", recommend_reason=why or b.get("recommend_reason"))
            items.append(b)
    send_json(handler, {"ok": bool(items), "reason": reason, "items": items})
