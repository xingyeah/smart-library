from typing import Any, Dict, List, Optional, Set, Tuple

from search.lexicon import _has_term
from semantic_models import get_bge_engine


def _finalize_ranked_pool(pool: List[Dict[str, Any]], max_results: int) -> List[Dict[str, Any]]:
    """Sort by raw score, keep one slot per work, cut the noisy tail, then assign rank and display score."""
    pool = sorted(pool, key=lambda x: x["raw_rank_score"], reverse=True)

    # One slot per work: another copy with the same full title, author and volume only crowds the page.
    # norm_title is not used: it drops the bracketed part, so a whole series ('牛津通识读本 (…)') would collapse.
    seen_works: Set[Tuple[str, str, str]] = set()
    unique_pool: List[Dict[str, Any]] = []
    for x in pool:
        work = ((x.get("title") or "").strip().lower(), (x.get("norm_author") or "").strip(), (x.get("volume_part") or "").strip())
        if work[0] and work in seen_works:
            continue
        seen_works.add(work)
        unique_pool.append(x)

    # Dynamic relative threshold cutoff so noise never leaks into results. The exact-title bonus (up to +480)
    # is left out of the basis: otherwise one exact match ('思考快与慢') cuts every related book.
    if unique_pool:
        basis = max(x["raw_rank_score"] - x.get("title_pin_bonus", 0.0) for x in unique_pool)
        min_cut = max(22.0, basis * 0.28)
        unique_pool = [x for x in unique_pool if x["raw_rank_score"] >= min_cut][:max_results]
    for x in unique_pool:
        x.pop("title_pin_bonus", None)

    max_raw = max((x["raw_rank_score"] for x in unique_pool), default=1.0)
    for idx_r, x in enumerate(unique_pool):
        rel = max(0.0, x["raw_rank_score"]) / max(60.0, max_raw)
        x["match_score"] = round(min(99.4, max(72.0, 75.0 + rel * 24.0 - idx_r * 0.25)), 1)
        x["rank"] = idx_r + 1
    return unique_pool



# Public and backward-compatible alias
finalize_ranked_pool = _finalize_ranked_pool


# Describe p50: Top-12 @160 tokens 1940 ms, Top-8 @128 992 ms; Top-8 still beats V1 nDCG with no class significantly worse
RERANK_TOP_N = 8
RERANK_MAX_TOKENS = 128


def rerank_document(item: Dict[str, Any]) -> str:
    """The text the cross-encoder reads for one book: title, author, field, AI keywords and points, blurb."""
    meta = item.get("_ai_meta") or {}
    field = " / ".join(x for x in (item.get("category"), meta.get("subfield")) if x)
    keywords = "、".join([k for k in (meta.get("keywords") or []) if isinstance(k, str)][:5])
    points = "；".join(p for p in (item.get("core_points") or [])[:2] if isinstance(p, str))
    parts = [
        f"《{item.get('title') or ''}》 {(item.get('subtitle') or '')[:45]}",
        f"作者：{(item.get('author') or '')[:25]}",
        f"领域：{field}",
    ]
    if keywords:
        parts.append(f"关键词：{keywords}")
    if points:
        parts.append(f"看点：{points}")
    parts.append((item.get("summary") or "")[:125])
    return " | ".join(parts)


def rerank_and_fuse_candidates(
    raw_query: str,
    intent: Dict[str, Any],
    prelim_pool: List[Dict[str, Any]],
    enable_cross_encoder: bool,
    max_results: int,
) -> List[Dict[str, Any]]:
    """
    Stage 4B: Fast Cross-Encoder Reranking on the top RERANK_TOP_N candidates.
    Stage 5: Final Score Fusion, Relevance Thresholding, Recommendation Reason Generation,
    and deduplicated final pooling.
    """
    primary_terms: List[str] = intent.get('primary_terms') or []
    synonym_terms: List[str] = intent.get('synonym_terms') or []
    wants_classic: bool = bool(intent.get('wants_classic'))
    needs_reranker: bool = bool(intent.get('needs_reranker')) and enable_cross_encoder
    bge = get_bge_engine()

    # Stage 4B: Single-Batch Fast Cross-Encoder Precision Reranking (bge-reranker-v2-m3) on the top RERANK_TOP_N candidates
    rerank_scores: Dict[int, float] = {}
    if needs_reranker and bge.is_reranker_ready() and prelim_pool:
        top_for_rerank = prelim_pool[:RERANK_TOP_N]
        rerank_docs: List[Tuple[int, str]] = []
        for item in top_for_rerank:
            rerank_docs.append((int(item["id"]), rerank_document(item)))
        ce_query = " ".join(primary_terms) if (wants_classic and len(primary_terms) == 1) else raw_query
        rerank_scores = bge.rerank_pairs(ce_query, rerank_docs, max_length=RERANK_MAX_TOKENS, batch_size=RERANK_TOP_N)

    # Stage 5: Final Score Fusion & Strict Relevance Thresholding
    final_pool: List[Dict[str, Any]] = []
    for idx_p, b in enumerate(prelim_pool):
        bid = int(b["id"])
        score = float(b["_prelim_score"])
        rr_prob = rerank_scores.get(bid)

        if rr_prob is not None:
            score += (rr_prob - 0.15) * 75.0
            if rr_prob < 0.03 and not b["_has_topical_hit"]:
                score -= 45.0

        b["raw_rank_score"] = round(score, 2)
        b["cosine_similarity"] = round(float(b["_cos_sim"]), 3)
        b["rerank_probability"] = round(float(rr_prob), 3) if rr_prob is not None else None

        # Relevance gate with Semantic Rescue: prune unrelated tail books while rescuing strong semantic/reranker matches
        if score < 18.0:
            continue
        semantic_rescued = (b["_cos_sim"] >= 0.55) or (rr_prob is not None and rr_prob >= 0.35)
        if not b["_has_topical_hit"] and not semantic_rescued:
            continue

        # Build transparent, human-readable recommendation/match reason
        reasons: List[str] = []
        if b["_matched_anchors"]:
            reasons.append(f"核心命中「{' / '.join(b['_matched_anchors'][:3])}」")
        if b["_matched_syns"]:
            reasons.append(f"跨语种/概念关联「{' / '.join(b['_matched_syns'][:3])}」")
        if b.get("_matched_groups", 0) >= 2:
            reasons.append(f"覆盖 {b['_matched_groups']} 项核心诉求")
        if wants_classic and float(b.get("classic_score") or 0) >= 35.0:
            reasons.append(f"经典指数 {round(float(b['classic_score']), 1)}")
        if b["_style_reason"]:
            reasons.append(b["_style_reason"])

        # Extract matching TOC chapters
        matched_chapters: List[str] = []
        chap_terms = [t.lower() for t in (primary_terms + synonym_terms[:6]) if len(t) >= 2]
        if b.get("toc_text") and chap_terms:
            for ch in b["toc_text"].split(" | "):
                ch_s = ch.strip()
                if any(_has_term(tk, ch_s.lower()) for tk in chap_terms):
                    matched_chapters.append(ch_s)
                    if len(matched_chapters) >= 2:
                        break
        if matched_chapters:
            reasons.append(f"相关章节：《{'》《'.join(matched_chapters[:2])}》")

        if not reasons:
            reasons.append(
                f"属「{b['category']}」高相关馆藏（语义匹配 {int(max(0.5, b['_cos_sim']) * 100)}%）"
            )

        b["matched_concepts"] = list(
            dict.fromkeys(b["_matched_anchors"] + b["_matched_syns"])
        )[:6]
        b["matched_chapters"] = matched_chapters
        b["recommend_reason"] = "；".join(reasons)

        # Clean internal temp keys
        for k in (
            "_prelim_score",
            "_lexical_score",
            "_cos_sim",
            "_has_topical_hit",
            "_in_target_cat",
            "_matched_anchors",
            "_matched_syns",
            "_matched_groups",
            "_style_reason",
            "_ai_meta",
        ):
            b.pop(k, None)

        final_pool.append(b)

    final_pool = _finalize_ranked_pool(final_pool, max_results)

    return final_pool
