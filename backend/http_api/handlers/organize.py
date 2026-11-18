from typing import Any, Dict, List

from archiver import rollback_organize_run
from database import get_conn
from ebook_parser import build_cjk_search_tokens, normalize_for_dedup
from http_api import state
from http_api.background import spawn_reindex, start_background_organize
from http_api.responses import send_json
from split_undo import undo_split_run
from mismerge_service import (
    SPLIT_RUN_TYPE,
    batch_split_mismerged,
    detect_mismerged_books,
    split_mismerged_book,
)


def handle_get_organize_status(handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any]) -> None:
    """GET /api/organize/status: Returns current scanning & enrichment progress."""
    with state._organize_lock:
        send_json(handler, dict(state._organize_state))


def handle_post_organize_start(handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any]) -> None:
    """POST /api/organize/start: Triggers background organizing process."""
    limit = body.get("limit_per_dir")
    limit_val = int(limit) if limit else None
    enable_ocr = bool(body.get("enable_ocr", False))
    res = start_background_organize(
        limit_per_dir=limit_val, run_type="manual", enable_ocr=enable_ocr
    )
    send_json(handler, res)


def handle_post_organize_rollback(handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any]) -> None:
    """POST /api/organize/rollback: Roll back an import run."""
    run_id = int(body.get("run_id") or 0)
    if run_id <= 0:
        send_json(handler, {"error": "Invalid run_id"}, 400)
        return
    conn = get_conn()
    try:
        run = conn.execute("SELECT run_type FROM organize_runs WHERE id = ?", (run_id,)).fetchone()
    finally:
        conn.close()
    if run and run["run_type"] == SPLIT_RUN_TYPE:  # unlinking alone would leave split records pointing nowhere
        try:
            send_json(handler, undo_split_run(run_id))
        except ValueError as e:
            send_json(handler, {"error": str(e)}, 400)
        return
    send_json(handler, rollback_organize_run(run_id))


def handle_get_conflicts(handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any]) -> None:
    """GET /api/conflicts: Retrieve pending or resolved metadata conflict entries."""
    status_filter = (qs.get("status") or ["pending"])[0]
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT c.*, b.title AS candidate_title, b.author AS candidate_author
            FROM conflict_inbox c
            LEFT JOIN books b ON c.candidate_book_id = b.id
            WHERE c.status = ?
            ORDER BY c.id DESC
            LIMIT 150
            """,
            (status_filter,),
        ).fetchall()
        send_json(handler, {"items": [dict(r) for r in rows]})
    finally:
        conn.close()


def handle_post_conflict_resolve(
    handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any], conflict_id: str
) -> None:
    """POST /api/conflicts/(?P<conflict_id>\\d+)/resolve: Resolve or ignore an inbox entry."""
    cid = int(conflict_id)
    action = body.get("action", "resolve")
    new_title = (body.get("title") or "").strip()
    new_author = (body.get("author") or "").strip()

    conn = get_conn()
    try:
        crow = conn.execute("SELECT * FROM conflict_inbox WHERE id = ?", (cid,)).fetchone()
        if not crow:
            send_json(handler, {"error": "Conflict not found"}, 404)
            return

        book_id = crow["candidate_book_id"]
        re_embed = False
        brow = conn.execute(
            "SELECT title, author, category, summary FROM books WHERE id = ?",
            (int(book_id or 0),),
        ).fetchone()

        if action == "update_book" and brow:
            title = new_title or brow["title"]
            author = new_author or brow["author"]
            nt, na = normalize_for_dedup(title, author)
            py_tokens = build_cjk_search_tokens(
                f"{title} {author} {brow['category'] or ''} {(brow['summary'] or '')[:300]}",
                include_pinyin=True,
            )
            conn.execute(
                "UPDATE books SET title = ?, author = ?, norm_title = ?, norm_author = ?, pinyin_search = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (title, author, nt, na, py_tokens, int(book_id)),
            )
            re_embed = True

        conn.execute("UPDATE conflict_inbox SET status = 'resolved' WHERE id = ?", (cid,))
        conn.commit()
    finally:
        conn.close()

    if re_embed and book_id:
        spawn_reindex([int(book_id)])
    send_json(handler, {"ok": True, "id": cid})


def handle_get_mismerged_books(handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any]) -> None:
    """GET /api/organize/mismerged: Detect multi-file books merged accidentally due to tag collisions."""
    limit_param = (qs.get("limit") or ["50"])[0]
    try:
        limit = max(1, min(200, int(limit_param)))
    except ValueError:
        limit = 50
    items = detect_mismerged_books(limit=limit)
    send_json(handler, {"items": items, "count": len(items)})


def handle_post_split_mismerged(handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any]) -> None:
    """POST /api/organize/split_mismerged: Split incompatible file clusters into independent books."""
    raw_bid = body.get("book_id")
    if raw_bid is not None:
        try:
            bid = int(raw_bid)
            res = split_mismerged_book(bid)
            status_code = 200 if res.get("ok") else 400
            send_json(handler, res, status_code)
            return
        except ValueError:
            send_json(handler, {"error": "Invalid book_id"}, 400)
            return

    if bool(body.get("batch", False)):
        limit = int(body.get("limit") or 20)
        res = batch_split_mismerged(limit=limit)
        send_json(handler, res)
        return

    send_json(handler, {"error": "Must provide book_id or batch: true"}, 400)

