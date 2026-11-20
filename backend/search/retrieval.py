import dataclasses
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from database import get_conn
from search.authors import register_author_sql
from search.filters import build_book_facet_where
from search.finalize import finalize_ranked_pool, _finalize_ranked_pool, rerank_and_fuse_candidates
from search.intent import analyze_query_intent, _translate_english_query
from search.recall import build_allowed_ids, execute_dual_path_recall
from search.scoring import pre_score_candidates
from search.types import ShelfFilters


def _execute_unified_hybrid_retrieval(raw_query: str, **options: Any) -> Dict[str, Any]:
    """Hybrid retrieval; English sentences are also searched in Chinese and both result lists merged."""
    res = _retrieve_one_language(raw_query, **options)
    zh_query = _translate_english_query(raw_query or "")
    if not zh_query:
        return res
    zh_res = _retrieve_one_language(zh_query, **options)
    best: Dict[int, Dict[str, Any]] = {}
    for item in res["items"] + zh_res["items"]:
        kept = best.get(int(item["id"]))
        if kept is None or item["raw_rank_score"] > kept["raw_rank_score"]:
            best[int(item["id"])] = item
    res["items"] = _finalize_ranked_pool(list(best.values()), int(options.get("max_results", 120)))
    res["total"] = len(res["items"])
    res["elapsed_ms"] = round(res["elapsed_ms"] + zh_res["elapsed_ms"], 2)
    res["translated_query"] = zh_query
    if not res.get("inferred_categories"):
        for key in ("inferred_category", "inferred_categories"):
            res[key] = zh_res.get(key)
    return res


def _retrieve_one_language(
    raw_query: str,
    filters: ShelfFilters = ShelfFilters(),
    max_results: int = 120,
    enable_cross_encoder: bool = True,
) -> Dict[str, Any]:
    """
    Unified Hybrid Search & Recommendation Pipeline:
      Stage 1: Query Intent Decomposition (topical anchors vs classic/style/anthology modifiers)
      Stage 2: Dual-Path Recall (FTS5 BM25 + Direct Topical SQL + bge-m3 1024-dim Multilingual Dense Vector)
      Stage 3: Reciprocal Rank Fusion (RRF) + Category Conflict Auto-Relaxation
      Stage 4: Multi-Signal Pre-scoring + Selective bge-reranker-v2-m3 Cross-Encoder Reranking
      Stage 5: Strict Relevance Cutoff Threshold & Deduplicated Pool Finalization
    """
    t0 = time.perf_counter()
    category = filters.category
    intent = analyze_query_intent(raw_query)
    inferred_cats: List[str] = intent.get("inferred_categories") or []

    # Smart Category Conflict Relaxation
    category_relaxed = False
    effective_category = category
    if category and category != "全部" and inferred_cats:
        if category not in inferred_cats:
            category_relaxed = True
            effective_category = ""

    conn = get_conn()
    register_author_sql(conn)
    try:
        # Build SQL filter for hard metadata facets (category, language, format, source, status, favorite, author)
        facet_clauses, facet_params = build_book_facet_where(dataclasses.replace(filters, category=effective_category))

        allowed_ids = build_allowed_ids(conn, facet_clauses, facet_params)
        if len(facet_clauses) > 1 and not allowed_ids:
            return {
                "items": [],
                "total": 0,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
                "intent": intent,
                "category_relaxed": category_relaxed,
                "requested_category": category,
                "inferred_category": inferred_cats[0] if inferred_cats else "",
                "inferred_categories": inferred_cats,
            }

        top_cand_ids, rows_by_id, ai_meta, dense_scores = execute_dual_path_recall(
            conn=conn,
            intent=intent,
            facet_clauses=facet_clauses,
            facet_params=facet_params,
            allowed_ids=allowed_ids,
        )
        if not top_cand_ids:
            return {
                "items": [],
                "total": 0,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
                "intent": intent,
                "category_relaxed": category_relaxed,
                "requested_category": category,
                "inferred_category": inferred_cats[0] if inferred_cats else "",
                "inferred_categories": inferred_cats,
            }

        prelim_pool = pre_score_candidates(
            raw_query=raw_query,
            intent=intent,
            top_cand_ids=top_cand_ids,
            rows_by_id=rows_by_id,
            ai_meta=ai_meta,
            dense_scores=dense_scores,
            category=category,
            inferred_cats=inferred_cats,
            category_relaxed=category_relaxed,
        )

        final_pool = rerank_and_fuse_candidates(
            raw_query=raw_query,
            intent=intent,
            prelim_pool=prelim_pool,
            enable_cross_encoder=enable_cross_encoder,
            max_results=max_results,
        )

        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        return {
            "items": final_pool,
            "total": len(final_pool),
            "elapsed_ms": elapsed_ms,
            "intent": intent,
            "category_relaxed": category_relaxed,
            "requested_category": category,
            "inferred_category": inferred_cats[0] if inferred_cats else "",
            "inferred_categories": inferred_cats,
        }
    finally:
        conn.close()
