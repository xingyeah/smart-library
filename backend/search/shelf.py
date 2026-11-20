import json
import time
from typing import Any, Dict, List, Optional

from database import get_conn
from ratings import composite_rating
from search.filters import (
    build_book_facet_where,
    build_pub_period_clause as _build_pub_period_clause,
    parse_language_filter as _parse_language_filter,
)
from search.authors import register_author_sql
from search.types import ShelfFilters, ShelfQuery


def _format_book_row(row: Any, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Format raw SQLite book record into JSON-serializable dictionary."""
    d = dict(row)
    d.pop("embedding_blob", None)
    d.pop("hyde_embedding_blob", None)
    d.pop("pinyin_search", None)
    for json_field in ("tags", "core_points", "hyde_queries"):
        val = d.get(json_field)
        if isinstance(val, str):
            try:
                d[json_field] = json.loads(val)
            except Exception:
                d[json_field] = []
        elif val is None:
            d[json_field] = []
    if isinstance(d.get("read_pos"), str):
        try:
            d["read_pos"] = json.loads(d["read_pos"])
        except Exception:
            d["read_pos"] = None
    if extra:
        d.update(extra)
    return d


format_book_row = _format_book_row


def load_ai_meta(conn: Any, ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """DeepSeek-enriched tags (book_ai_meta, backend/enrich_ai.py); empty when the table or rows are missing."""
    if not ids:
        return {}
    try:
        rows = conn.execute(
            f"SELECT * FROM book_ai_meta WHERE book_id IN ({','.join('?' * len(ids))})", list(ids)
        ).fetchall()
    except Exception:
        return {}
    meta = {}
    for r in rows:
        try:
            kws = json.loads(r["keywords"] or "[]")
        except Exception:
            kws = []
        meta[int(r["book_id"])] = {
            "clean_title": r["clean_title"] or "",
            "keywords": kws,
            "level": r["level"] or "",
            "kind": r["kind"] or "",
            "ai_category": r["ai_category"] or "",
            "subfield": r["subfield"] or "",
        }
    return meta


def load_external_ratings(conn: Any, ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
    """Every external rating above zero of the given book IDs (book_external_meta), per book."""
    if not ids:
        return {}
    try:
        rows = conn.execute(
            f"SELECT book_id, source, rating, votes FROM book_external_meta WHERE book_id IN ({','.join('?' * len(ids))}) AND rating > 0",
            list(ids),
        ).fetchall()
        by_book: Dict[int, List[Dict[str, Any]]] = {}
        for r in rows:
            by_book.setdefault(int(r["book_id"]), []).append(
                {"source": r["source"], "rating": float(r["rating"]), "votes": int(r["votes"] or 0)})
        return by_book
    except Exception:
        return {}


def _rating_sources(book: Dict[str, Any], external: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """External ratings plus the Douban one kept on the book row, which older enrichment stored only there."""
    sources = list(external)
    douban = float(book.get("douban_rating") or 0)
    if douban > 0 and not any(s["source"] == "douban" for s in sources):
        sources.insert(0, {"source": "douban", "rating": douban, "votes": int(book.get("douban_votes") or 0)})
    return sources


def attach_ratings(items: List[Dict[str, Any]], conn: Any) -> List[Dict[str, Any]]:
    """Adds rating_sources and the composite_rating shown on the card, for books that have any rating."""
    ext_ratings = load_external_ratings(conn, [int(b["id"]) for b in items if b.get("id") is not None])
    for b in items:
        sources = _rating_sources(b, ext_ratings.get(int(b["id"]), [])) if b.get("id") is not None else []
        if sources:
            b["rating_sources"] = sources
            b["composite_rating"] = composite_rating(sources)
    return items


def attach_ai_meta(items: List[Dict[str, Any]], conn: Any = None) -> List[Dict[str, Any]]:
    """Adds clean_title / ai_level / ai_kind and external ratings for display."""
    own = conn is None
    conn = conn or get_conn()
    try:
        meta = load_ai_meta(conn, [int(b["id"]) for b in items if b.get("id") is not None])
        attach_ratings(items, conn)
    finally:
        if own:
            conn.close()
    for b in items:
        m = meta.get(int(b["id"])) if b.get("id") is not None else None
        if m:
            b["clean_title"] = m["clean_title"]
            b["ai_level"] = m["level"]
            b["ai_kind"] = m["kind"]
    return items


def apply_shelf_sort(items: List[Dict[str, Any]], sort_by: str) -> None:
    """Sort retrieved books in-place according to user-selected dropdown."""
    if sort_by in ("quality", "classic"):
        items.sort(
            key=lambda x: (float(x.get("classic_score") or 0), float(x.get("quality_score") or 0)),
            reverse=True,
        )
    elif sort_by == "title":
        items.sort(key=lambda x: x.get("norm_title") or "")
    elif sort_by == "pub_desc":
        items.sort(
            key=lambda x: (
                int(x["pub_year"]) if (x.get("pub_year") and x["pub_year"].isdigit()) else 0,
                float(x.get("classic_score") or 0),
            ),
            reverse=True,
        )
    elif sort_by == "pub_asc":
        items.sort(
            key=lambda x: (
                int(x["pub_year"]) if (x.get("pub_year") and x["pub_year"].isdigit()) else 9999,
                -float(x.get("classic_score") or 0),
            ),
        )
    elif sort_by == "opened":
        items.sort(
            key=lambda x: (x.get("last_opened_at") or "", int(x.get("id") or 0)),
            reverse=True,
        )
    elif sort_by in ("imported", "imported_asc"):
        items.sort(
            key=lambda x: (x.get("created_at") or "", int(x.get("id") or 0)),
            reverse=sort_by == "imported",
        )
    elif sort_by == "rating":
        items.sort(
            key=lambda x: (float(x.get("user_rating") or 0), float(x.get("classic_score") or 0)),
            reverse=True,
        )


def search_empty_shelf(query: ShelfQuery = ShelfQuery(), start_time: float = 0.0) -> Dict[str, Any]:
    """Execute fast SQL faceted search when query string is empty (query.q is ignored)."""
    t0 = start_time or time.perf_counter()
    sort_by, page, page_size = query.sort_by, query.page, query.page_size
    conn = get_conn()
    register_author_sql(conn)
    try:
        where_clauses, params = build_book_facet_where(query.filters, table_alias="b")

        order_map = {
            "relevance": "b.classic_score DESC, b.quality_score DESC, b.id DESC",
            "classic": "b.classic_score DESC, b.quality_score DESC, b.id DESC",
            "quality": "b.classic_score DESC, b.quality_score DESC, b.id DESC",
            "pub_desc": "CASE WHEN b.pub_year GLOB '[12][0-9][0-9][0-9]' THEN CAST(b.pub_year AS INT) ELSE 0 END DESC, b.classic_score DESC",
            "pub_asc": "CASE WHEN b.pub_year GLOB '[12][0-9][0-9][0-9]' THEN CAST(b.pub_year AS INT) ELSE 9999 END ASC, b.classic_score DESC",
            "title": "b.norm_title ASC",
            "imported": "b.created_at DESC, b.id DESC",
            "imported_asc": "b.created_at ASC, b.id ASC",
            "opened": "b.last_opened_at IS NULL, b.last_opened_at DESC, b.id DESC",
            "rating": "b.user_rating DESC, b.classic_score DESC",
        }
        order_clause = order_map.get(sort_by, "b.classic_score DESC, b.quality_score DESC, b.id DESC")
        offset = max(0, (page - 1) * page_size)

        total = int(
            conn.execute(
                f"SELECT COUNT(*) FROM books b WHERE {' AND '.join(where_clauses)}",
                params,
            ).fetchone()[0]
        )
        rows = conn.execute(
            f"SELECT b.* FROM books b WHERE {' AND '.join(where_clauses)} ORDER BY {order_clause} LIMIT ? OFFSET ?",
            [*params, page_size, offset],
        ).fetchall()

        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "elapsed_ms": elapsed_ms,
            "is_semantic_intent": False,
            "category_relaxed": False,
            "items": attach_ai_meta([_format_book_row(r) for r in rows], conn),
        }
    finally:
        conn.close()
