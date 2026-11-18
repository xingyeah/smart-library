import logging
import time
from typing import Any, Dict, List

from search.corpus_stats import (
    ensure_author_lexicon as _ensure_author_lexicon,
    ensure_corpus_idf_cache as _ensure_corpus_idf_cache,
)
from search.retrieval import _execute_unified_hybrid_retrieval
from semantic_models import get_bge_engine


def _apply_author_diversity(pool: List[Dict[str, Any]], desc: str, top_k: int) -> List[Dict[str, Any]]:
    """MMR Author & Series Diversity Filter (cap max 2 per author unless author is in query)."""
    selected: List[Dict[str, Any]] = []
    author_counts: Dict[str, int] = {}
    deferred: List[Dict[str, Any]] = []

    for item in pool:
        norm_a = item.get("norm_author") or "未知作者"
        limit = 5 if (norm_a != "未知作者" and norm_a in desc.lower()) else 2
        cnt = author_counts.get(norm_a, 0)
        if norm_a != "未知作者" and cnt >= limit:
            deferred.append(item)
            continue
        author_counts[norm_a] = cnt + 1
        selected.append(item)
        if len(selected) >= top_k:
            break

    for item in deferred:
        if len(selected) >= top_k:
            break
        selected.append(item)
    return selected


def recommend_by_description(
    description: str, top_k: int = 10, language: str = "", cross_encoder: bool = True
) -> Dict[str, Any]:
    """
    Top-K Precision Recommendation by Natural Language Description:
    Uses the Unified Hybrid Retrieval + Cross-Encoder Reranking + MMR Author Diversity.
    """
    t0 = time.perf_counter()
    desc = (description or "").strip()
    if not desc:
        return {"items": [], "elapsed_ms": 0.0}

    hybrid_res = _execute_unified_hybrid_retrieval(
        raw_query=desc, language=language, max_results=max(60, top_k * 4),
        enable_cross_encoder=cross_encoder,
    )
    selected_top = _apply_author_diversity(hybrid_res["items"], desc, top_k)
    for idx, item in enumerate(selected_top):
        item["rank"] = idx + 1

    intent = hybrid_res["intent"]
    return {
        "query": desc,
        "inferred_categories": hybrid_res["inferred_categories"],
        "expanded_tokens": intent["primary_terms"],
        "expanded_neighbors": {s: 1.0 for s in intent["synonym_terms"][:10]},
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        "items": selected_top,
    }


def warmup_search_engine() -> None:
    """Pre-warm Corpus IDF cache, bge-m3 embedder, vector matrix, and bge-reranker-v2-m3 on startup."""
    try:
        _ensure_corpus_idf_cache()
        _ensure_author_lexicon()
        bge = get_bge_engine()
        bge.load_vector_index()
        if bge.is_embedder_ready():
            bge.encode_query("心理学与认知科学")
        if bge.is_reranker_ready():
            bge.rerank_pairs("心理学", [(1, "心理学与脑科学")], max_length=32, batch_size=1)
    except Exception:
        # Non-critical background warmup failure must not block server bootstrap
        logging.getLogger(__name__).warning("search warm-up failed; the first query will be slow", exc_info=True)
