"""
search_engine.py: 兼容门面。
核心逻辑已解耦重构至 backend/search/ 模块。
"""

from typing import Any

import search
from search import (
    ShelfFilters,
    ShelfQuery,
    _execute_unified_hybrid_retrieval,
    _format_book_row,
    _retrieve_one_language,
    analyze_query_intent,
    attach_ai_meta,
    build_fts_query,
    format_book_row,
    get_corpus_idf_mult,
    infer_query_categories,
    instant_search,
    load_ai_meta,
    recommend_by_description,
    interest_candidates,
    recommend_by_interests,
    reset_corpus_caches,
    search_shelf,
    warmup_search_engine,
)
from search.corpus_stats import (
    ensure_author_lexicon as _ensure_author_lexicon,
    ensure_corpus_idf_cache as _ensure_corpus_idf_cache,
)
from search.finalize import finalize_ranked_pool as _finalize_ranked_pool
from search.intent import (
    _QUERY_TRANSLATION_CACHE,
    _detect_pinyin_query,
    _translate_english_query,
)
from search.lexicon import (
    _has_term,
    CONVERSATIONAL_STOPWORDS,
    DOMAIN_SYNONYM_MAP,
)
from search.shelf import _build_pub_period_clause, _parse_language_filter


def __getattr__(name: str) -> Any:
    """Dynamic fallback for internal variables or constants."""
    if hasattr(search, name):
        return getattr(search, name)
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = [
    "ShelfFilters",
    "ShelfQuery",
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
    "_finalize_ranked_pool",
    "_ensure_corpus_idf_cache",
    "_ensure_author_lexicon",
    "_detect_pinyin_query",
    "_translate_english_query",
    "_QUERY_TRANSLATION_CACHE",
    "_parse_language_filter",
    "_build_pub_period_clause",
    "_has_term",
]
