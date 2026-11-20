import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import deepseek_client
from auto_summary_refresher import enqueue_book_summary_refresh
from database import get_conn
from ebook_parser import build_cjk_search_tokens, normalize_for_dedup
from http_api import state
from http_api.background import spawn_reindex
from http_api.responses import send_json
from ratings import composite_rating
from search import ShelfFilters, ShelfQuery, search_shelf
from search.authors import author_links
from search.shelf import load_ai_meta
from search_engine import _format_book_row as format_book_row
from semantic_models import get_bge_engine

logger = logging.getLogger("http_api.books")


def get_similar_books(book_id: int, top_k: int = 6) -> List[Dict[str, Any]]:
    """Retrieve dense vector nearest neighbors for a book using bge embeddings."""
    engine = get_bge_engine()
    valid_ids, vecs = engine.get_book_vectors([book_id])
    if not valid_ids or vecs.shape[0] == 0:
        return []
    conn = get_conn()
    try:
        # Left in the ranking, deleted books would take places and the list would come up short
        deleted_ids = {int(row["id"]) for row in conn.execute("SELECT id FROM books WHERE is_deleted = 1")}
        paired = engine.dense_search_by_vector(vecs[0], exclude_ids=deleted_ids | {book_id}, top_k=top_k)
        if not paired:
            return []
        top_ids = [bid for bid, _ in paired]
        placeholders = ",".join("?" for _ in top_ids)
        sim_rows = conn.execute(
            f"SELECT * FROM books WHERE id IN ({placeholders}) AND is_deleted = 0", top_ids
        ).fetchall()
        by_id = {int(r["id"]): format_book_row(r) for r in sim_rows}
        meta = load_ai_meta(conn, list(by_id))
        for bid, item in by_id.items():
            item["clean_title"] = (meta.get(bid) or {}).get("clean_title") or ""
        result = []
        for bid, sc in paired:
            if bid in by_id:
                item = by_id[bid]
                item["similarity"] = round(max(0.0, sc) * 100, 1)
                result.append(item)
        return result
    finally:
        conn.close()


def _with_clean_title(conn: Any, book: Dict[str, Any]) -> Dict[str, Any]:
    """The detail shows the same display title as the cards."""
    meta = load_ai_meta(conn, [int(book["id"])]).get(int(book["id"]))
    book["clean_title"] = (meta or {}).get("clean_title") or ""
    return book


def _qint(raw: Any, default: int, minimum: Optional[int] = None, maximum: Optional[int] = None) -> int:
    """Parse an int from a request value; ValueError on junk, empty means default, clamped to [minimum, maximum]."""
    v = default if (raw is None or raw == "") else int(raw)
    if minimum is not None and v < minimum:
        v = minimum
    if maximum is not None and v > maximum:
        v = maximum
    return v


def handle_get_stats(handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any]) -> None:
    """GET /api/stats: Aggregate catalog-wide collection stats."""
    conn = get_conn()
    try:
        total_books = conn.execute("SELECT COUNT(*) FROM books WHERE is_deleted = 0").fetchone()[0]
        total_files = conn.execute(
            "SELECT COUNT(*) FROM book_files bf JOIN books b ON bf.book_id = b.id WHERE b.is_deleted = 0"
        ).fetchone()[0]
        total_bytes = conn.execute(
            "SELECT COALESCE(SUM(bf.file_size), 0) FROM book_files bf JOIN books b ON bf.book_id = b.id WHERE b.is_deleted = 0"
        ).fetchone()[0]
        fav_count = conn.execute("SELECT COUNT(*) FROM books WHERE is_favorite = 1 AND is_deleted = 0").fetchone()[0]
        pending_conflicts = conn.execute(
            "SELECT COUNT(*) FROM conflict_inbox WHERE status = 'pending'"
        ).fetchone()[0]

        cats = [
            {"category": r["category"], "count": r["cnt"]}
            for r in conn.execute(
                "SELECT category, COUNT(*) AS cnt FROM books WHERE is_deleted = 0 GROUP BY category ORDER BY cnt DESC"
            ).fetchall()
        ]
        formats = [
            {"format": r["primary_format"], "count": r["cnt"]}
            for r in conn.execute(
                "SELECT primary_format, COUNT(*) AS cnt FROM books WHERE is_deleted = 0 GROUP BY primary_format ORDER BY cnt DESC"
            ).fetchall()
        ]
        sources = [
            {"source": r["source_dir"], "count": r["cnt"]}
            for r in conn.execute(
                "SELECT source_dir, COUNT(*) AS cnt FROM books WHERE is_deleted = 0 GROUP BY source_dir ORDER BY cnt DESC"
            ).fetchall()
        ]
        statuses = {
            r["reading_status"]: r["cnt"]
            for r in conn.execute(
                "SELECT reading_status, COUNT(*) AS cnt FROM books WHERE is_deleted = 0 GROUP BY reading_status"
            ).fetchall()
        }
        languages = {
            r["language"]: r["cnt"]
            for r in conn.execute(
                "SELECT language, COUNT(*) AS cnt FROM books WHERE is_deleted = 0 GROUP BY language"
            ).fetchall()
        }
        recent_runs = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM organize_runs ORDER BY id DESC LIMIT 8"
            ).fetchall()
        ]
        with state._organize_lock:
            org_state = dict(state._organize_state)

        send_json(
            handler,
            {
                "total_books": total_books,
                "total_files": total_files,
                "duplicates_merged": max(0, total_files - total_books),
                "total_size_gb": round(total_bytes / (1024 ** 3), 2),
                "favorite_count": fav_count,
                "pending_conflicts": pending_conflicts,
                "categories": cats,
                "formats": formats,
                "sources": sources,
                "languages": languages,
                "reading_statuses": statuses,
                "recent_runs": recent_runs,
                "organize_state": org_state,
            },
        )
    finally:
        conn.close()


def handle_get_ai_usage(handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any]) -> None:
    """GET /api/ai/usage: Summarize DeepSeek LLM token usage."""
    send_json(handler, deepseek_client.usage_summary())


def handle_get_books(handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any]) -> None:
    """GET /api/books: Shelf instant search and faceted filtering."""
    q_str = lambda k, d="": (qs.get(k) or [d])[0]
    try:
        page = _qint(q_str("page", "1"), 1, minimum=1)
        page_size = _qint(q_str("page_size", "24"), 24, minimum=1, maximum=200)
    except ValueError:
        send_json(handler, {"error": "page / page_size must be integers"}, 400)
        return

    filters = ShelfFilters(
        category=q_str("category"), language=q_str("language"), fmt=q_str("format"),
        source_dir=q_str("source_dir"), reading_status=q_str("reading_status"),
        only_favorite=q_str("only_favorite") in ("1", "true"), pub_period=q_str("pub_period"),
        has_rating=q_str("has_rating") in ("1", "true"), author=q_str("author").strip(),
    )
    query = ShelfQuery(q=q_str("q"), filters=filters, sort_by=q_str("sort_by", "relevance"), page=page, page_size=page_size)
    send_json(handler, search_shelf(query))


def _extract_reviews_from_row(row_reviews_json: str) -> List[Dict[str, Any]]:
    try:
        data = json.loads(row_reviews_json or "[]")
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _build_external_meta(conn, bid: int, book_row: Optional[Any] = None) -> Dict[str, Any]:
    """Assemble ratings and reviews from book_external_meta table."""
    ext_rows = conn.execute(
        "SELECT * FROM book_external_meta WHERE book_id = ? ORDER BY id ASC",
        (bid,),
    ).fetchall()
    ratings = []
    reviews = []
    has_douban = False
    for r in ext_rows:
        if r["status"] != "success":
            continue
        if r["source"] == "douban":
            has_douban = True
        ratings.append({
            "source": r["source"],
            "rating": r["rating"],
            "raw_rating": r["raw_rating"],
            "votes": r["votes"],
        })
        reviews.extend(_extract_reviews_from_row(r["reviews_json"]))

    if not has_douban and book_row:
        d_rating = float(book_row["douban_rating"] or 0)
        if d_rating > 0:
            ratings.insert(0, {
                "source": "douban",
                "rating": d_rating,
                "raw_rating": d_rating,
                "votes": int(book_row["douban_votes"] or 0),
            })
    return {"ratings": ratings, "reviews": reviews, "composite": composite_rating(ratings)}


def handle_get_book_detail(
    handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any], book_id: str
) -> None:
    """GET /api/books/(?P<book_id>\\d+): Fetch metadata and file copies; similar books come from /similar."""
    bid = int(book_id)
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM books WHERE id = ?", (bid,)).fetchone()
        if not row or int(row["is_deleted"] or 0) == 1:
            send_json(handler, {"error": "Book not found"}, 404)
            return
        files = [
            dict(f)
            for f in conn.execute(
                "SELECT * FROM book_files WHERE book_id = ? ORDER BY is_primary DESC",
                (bid,),
            ).fetchall()
        ]
        b = _with_clean_title(conn, format_book_row(row, {"files": files, "authors": author_links(row["author"] or "")}))
        b["external_meta"] = _build_external_meta(conn, bid, row)
    finally:
        conn.close()
    send_json(handler, b)


def handle_get_similar_books(
    handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any], book_id: str
) -> None:
    """GET /api/books/(?P<book_id>\\d+)/similar: Vector neighbours, fetched apart so the detail shows first."""
    send_json(handler, {"items": get_similar_books(int(book_id), top_k=6)})


def handle_post_book_progress(
    handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any], book_id: str
) -> None:
    """POST /api/books/(?P<book_id>\\d+)/progress: Save reader progress."""
    bid = int(book_id)
    try:
        prog = body.get("progress")
        prog = None if prog is None else min(1.0, max(0.0, float(prog)))
        pos = {
            "chapter_idx": max(0, int(body.get("chapter_idx") or 0)),
            "offset": min(1.0, max(0.0, float(body.get("offset") or 0))),
            "pdf_page": max(1, int(body["pdf_page"])) if body.get("pdf_page") else None,
            "label": str(body.get("label") or "")[:80],
        }
        ts = float(body["opened_at"]) / 1000 if body.get("opened_at") else time.time()
    except (TypeError, ValueError):
        send_json(handler, {"error": "bad progress"}, 400)
        return

    opened = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(min(ts, time.time())))
    conn = get_conn()
    try:
        cur = conn.execute(
            "UPDATE books SET last_opened_at = ?, read_progress = COALESCE(?, read_progress), read_pos = ?, "
            # Opening a book that was not being read marks it as reading: that is a mark, made at this opening.
            # Listed before reading_status so the CASE sees the status as it was (SQLite evaluates SET in order
            # against the old row either way)
            "marked_at = CASE WHEN reading_status IN ('read', 'reading') THEN marked_at ELSE ? END, "
            "reading_status = CASE WHEN reading_status IN ('read', 'reading') THEN reading_status ELSE 'reading' END "
            "WHERE id = ? AND (last_opened_at IS NULL OR last_opened_at <= ?)",
            (opened, prog, json.dumps(pos, ensure_ascii=False), opened, bid, opened),
        )
        conn.commit()
    finally:
        conn.close()
    if cur.rowcount > 0:
        enqueue_book_summary_refresh(bid)
    send_json(handler, {"ok": True, "updated": cur.rowcount})


UNREAD = "unread"
RATING_MIN, RATING_MAX = 0, 5


def _posted_marks(body: Dict[str, Any]) -> Dict[str, Any]:
    """books column -> value to store, for each of the three marks the body carries (reading status, favourite,
    rating). ValueError or TypeError when the rating is not a number."""
    marks: Dict[str, Any] = {}
    if body.get("reading_status") is not None:
        marks["reading_status"] = str(body["reading_status"]).strip()
    if "is_favorite" in body:
        marks["is_favorite"] = 1 if body["is_favorite"] else 0
    if "user_rating" in body:
        marks["user_rating"] = max(RATING_MIN, min(RATING_MAX, int(body["user_rating"])))
    return marks


def _is_new_mark(row: Any, marks: Dict[str, Any]) -> bool:
    """Whether `marks` gives the book a mark it does not have now. The same value sent again is none, and neither
    is taking a mark away: marked_at is when the reader last marked the book, not when they last touched it."""
    unmarked = {"reading_status": UNREAD, "is_favorite": 0, "user_rating": RATING_MIN}
    return any(value != unmarked[column] and value != (row[column] or unmarked[column])
               for column, value in marks.items())


def handle_post_book_meta(
    handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any], book_id: str
) -> None:
    """POST /api/books/(?P<book_id>\\d+)/meta: Update editable metadata, ratings, favorites, notes."""
    bid = int(book_id)
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM books WHERE id = ?", (bid,)).fetchone()
        if not row or int(row["is_deleted"] or 0) == 1:
            send_json(handler, {"error": "Book not found"}, 404)
            return

        if body.get("reading_status") is not None and body["reading_status"] not in (
            "unread",
            "want",
            "reading",
            "read",
        ):
            send_json(handler, {"error": "reading_status must be unread, want, reading or read"}, 400)
            return

        updates = []
        params = []
        re_embed = False

        for field in (
            "title",
            "subtitle",
            "author",
            "publisher",
            "pub_year",
            "category",
            "summary",
            "user_notes",
        ):
            if field in body and body[field] is not None:
                updates.append(f"{field} = ?")
                params.append(str(body[field]).strip())
                if field in ("title", "subtitle", "author", "category", "summary"):
                    re_embed = True
        if "summary" in body and body["summary"] is not None:
            updates.append("summary_source = ?")
            params.append("manual")

        marks = _posted_marks(body)
        for column, value in marks.items():
            updates.append(f"{column} = ?")
            params.append(value)
        if _is_new_mark(row, marks):
            updates.append("marked_at = CURRENT_TIMESTAMP")
        if "tags" in body and isinstance(body["tags"], list):
            updates.append("tags = ?")
            params.append(json.dumps(body["tags"], ensure_ascii=False))
            re_embed = True

        if re_embed:
            new_title = str(body.get("title") or row["title"])
            new_author = str(body.get("author") or row["author"])
            new_cat = str(body.get("category") or row["category"])
            new_sum = str(body.get("summary") or row["summary"])
            nt, na = normalize_for_dedup(new_title, new_author)
            updates.extend(["norm_title = ?", "norm_author = ?", "pinyin_search = ?"])
            py_tokens = build_cjk_search_tokens(
                f"{new_title} {new_author} {new_cat} {new_sum[:300]}", include_pinyin=True
            )
            params.extend([nt, na, py_tokens])

        stale_archived = ""
        new_title = str(body.get("title") or "").strip()
        if new_title and new_title != row["title"]:
            from book_enrichment_service import relocate_archived_hardlink  # lazy: it imports this package

            new_archived, stale_archived = relocate_archived_hardlink(conn, row, new_title)
            updates.append("archived_path = ?")
            params.append(new_archived)

        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            sql = f"UPDATE books SET {', '.join(updates)} WHERE id = ?"
            params.append(bid)
            conn.execute(sql, params)
            conn.commit()

        updated_row = conn.execute("SELECT * FROM books WHERE id = ?", (bid,)).fetchone()
        res_book = _with_clean_title(conn, format_book_row(updated_row))
    finally:
        conn.close()
    if stale_archived:
        from book_enrichment_service import remove_stale_hardlink

        remove_stale_hardlink(stale_archived)

    if re_embed:
        spawn_reindex([bid])
    if "summary" in body and body["summary"] is not None:
        from catalog_sync import enqueue_catalog_sync

        enqueue_catalog_sync()
    if body.get("is_favorite") or body.get("reading_status") in ("want", "reading"):
        enqueue_book_summary_refresh(bid)
    send_json(handler, res_book)


def handle_post_book_enrich_refresh(
    handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any], book_id: str
) -> None:
    """POST /api/books/(?P<book_id>\\d+)/enrich_refresh: Trigger ground-truth AI metadata re-generation."""
    bid = int(book_id)
    try:
        from book_enrichment_service import refresh_book_ai_metadata
        res = refresh_book_ai_metadata(bid)
        conn = get_conn()
        try:
            row = conn.execute("SELECT * FROM books WHERE id = ?", (bid,)).fetchone()
            files = [
                dict(f) for f in conn.execute(
                    "SELECT * FROM book_files WHERE book_id = ? ORDER BY is_primary DESC", (bid,)
                ).fetchall()
            ]
            formatted = _with_clean_title(conn, format_book_row(row, {"files": files}))
        finally:
            conn.close()
        formatted["similar_books"] = get_similar_books(bid, top_k=6)
        send_json(handler, {"ok": True, "book": formatted, "enriched": res})
    except Exception as e:
        status, message = _enrich_refresh_error(e)
        if status == 500:
            logger.exception("Failed AI refresh for book %s", bid)
        send_json(handler, {"ok": False, "error": message}, status=status)


def _enrich_refresh_error(error: Exception) -> Tuple[int, str]:
    """HTTP status and user-facing text for a failed AI refresh."""
    import socket
    import urllib.error
    import deepseek_client
    from book_enrichment_service import BookNotFoundError, NoReadableTextError

    if isinstance(error, BookNotFoundError):
        return 404, "没有这本书"
    if isinstance(error, NoReadableTextError):
        return 422, "读不到这本书的正文，无法根据内容刷新，书目信息未改动"
    if isinstance(error, deepseek_client.DailyCapReached):
        return 429, "今日 AI 额度已用完"
    if isinstance(error, RuntimeError) and "DEEPSEEK_API_KEY" in str(error):
        return 503, "未配置 DeepSeek API Key"
    if isinstance(error, (TimeoutError, socket.timeout)) or "timed out" in str(error).lower():
        return 504, "AI 请求超时，请稍后重试"
    if isinstance(error, urllib.error.URLError):
        return 502, f"AI 网络连接失败：{getattr(error, 'reason', error)}"
    return 500, f"AI 刷新异常：{str(error)[:80]}"


def handle_post_book_delete(
    handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any], book_id: str
) -> None:
    """POST /api/books/(?P<book_id>\\d+)/delete: Soft-delete book and relocate physical files."""
    bid = int(book_id)
    try:
        from book_removal_service import (
            BookAlreadyDeletedError,
            BookNotFoundError,
            DeleteNeedsConfirmation,
            remove_book_and_relocate_files,
        )
        raw_ids = body.get("confirmed_file_ids")
        if raw_ids is not None and not (isinstance(raw_ids, list) and all(isinstance(i, int) for i in raw_ids)):
            send_json(handler, {"error": "confirmed_file_ids must be a list of integers"}, 400)
            return
        res = remove_book_and_relocate_files(bid, raw_ids)
        send_json(handler, res)
    except DeleteNeedsConfirmation as need:
        send_json(handler, {"error": "这本书有多个文件，请确认文件清单后再删除", "needs_confirmation": True, "files": need.files}, 409)
    except BookNotFoundError:
        send_json(handler, {"error": "图书不存在"}, 404)
    except BookAlreadyDeletedError:
        send_json(handler, {"error": "图书已在回收目录中，无需重复删除"}, 400)
    except Exception as e:
        logger.exception("Failed removing book %s", bid)
        send_json(handler, {"error": f"删除失败: {str(e)[:80]}"}, 500)


