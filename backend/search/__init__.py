import time
from typing import Any, Dict, List, Optional

from database import get_conn

from search.corpus_stats import (
    ensure_author_lexicon,
    ensure_corpus_idf_cache,
    get_corpus_idf_mult,
    reset_corpus_caches,
)
from search.filters import (
    build_book_facet_where,
    build_language_clause,
    build_pub_period_clause,
    parse_language_filter,
)
from search.finalize import finalize_ranked_pool
from search.intent import (
    analyze_query_intent,
    infer_query_categories,
)
from search.recall import build_fts_query
from search.interest import interest_candidates, recommend_by_interests
from search.recommend import (
    recommend_by_description,
    warmup_search_engine,
)
from search.retrieval import (
    _execute_unified_hybrid_retrieval,
    _retrieve_one_language,
)
from search.shelf import (
    _build_pub_period_clause,
    _format_book_row,
    _parse_language_filter,
    apply_shelf_sort,
    attach_ai_meta,
    attach_ratings,
    format_book_row,
    load_ai_meta,
    search_empty_shelf,
)
from search.types import ShelfFilters, ShelfQuery


def search_shelf(query: ShelfQuery) -> Dict[str, Any]:
    """Execute shelf browsing or unified hybrid search given a ShelfQuery parameter object."""
    t0 = time.perf_counter()
    q_clean = (query.q or "").strip()
    f = query.filters
    lang_list = _parse_language_filter(f.language)

    if q_clean:
        hybrid_res = _execute_unified_hybrid_retrieval(
            raw_query=q_clean, category=f.category, language=f.language, fmt=f.fmt,
            source_dir=f.source_dir, reading_status=f.reading_status,
            only_favorite=f.only_favorite, pub_period=f.pub_period,
            has_rating=f.has_rating,
            max_results=120, enable_cross_encoder=False,
        )
        all_items = hybrid_res["items"]
        apply_shelf_sort(all_items, query.sort_by)
        offset = max(0, (query.page - 1) * query.page_size)
        page_items = all_items[offset : offset + query.page_size]
        conn = get_conn()
        try:
            attach_ratings(page_items, conn)
        finally:
            conn.close()

        return {
            "total": len(all_items), "page": query.page, "page_size": query.page_size,
            "elapsed_ms": hybrid_res["elapsed_ms"], "is_semantic_intent": True,
            "category_relaxed": hybrid_res["category_relaxed"],
            "requested_category": hybrid_res["requested_category"],
            "inferred_category": hybrid_res["inferred_category"],
            "inferred_categories": hybrid_res["inferred_categories"],
            "items": page_items,
        }

    return search_empty_shelf(
        category=f.category, lang_list=lang_list, fmt=f.fmt,
        source_dir=f.source_dir, reading_status=f.reading_status,
        only_favorite=f.only_favorite, pub_period=f.pub_period,
        has_rating=f.has_rating,
        sort_by=query.sort_by, page=query.page, page_size=query.page_size, start_time=t0,
    )


def instant_search(
    q: str = "",
    category: str = "",
    language: str = "",
    fmt: str = "",
    source_dir: str = "",
    reading_status: str = "",
    only_favorite: bool = False,
    pub_period: str = "",
    has_rating: bool = False,
    sort_by: str = "relevance",
    page: int = 1,
    page_size: int = 24,
) -> Dict[str, Any]:
    """Backward-compatible entry point forwarding flat arguments to search_shelf."""
    query = ShelfQuery(
        q=q,
        filters=ShelfFilters(
            category=category,
            language=language,
            fmt=fmt,
            source_dir=source_dir,
            reading_status=reading_status,
            only_favorite=only_favorite,
            pub_period=pub_period,
            has_rating=has_rating,
        ),
        sort_by=sort_by,
        page=page,
        page_size=page_size,
    )
    return search_shelf(query)


__all__ = [
    "ShelfFilters",
    "ShelfQuery",
    "build_book_facet_where",
    "build_language_clause",
    "build_pub_period_clause",
    "parse_language_filter",
    "search_shelf",
    "instant_search",
    "recommend_by_description",
    "interest_candidates",
    "recommend_by_interests",
    "warmup_search_engine",
    "analyze_query_intent",
    "build_fts_query",
    "infer_query_categories",
    "load_ai_meta",
    "attach_ai_meta",
    "reset_corpus_caches",
    "get_corpus_idf_mult",
    "format_book_row",
    "_format_book_row",
    "_execute_unified_hybrid_retrieval",
    "_retrieve_one_language",
    "finalize_ranked_pool",
]
