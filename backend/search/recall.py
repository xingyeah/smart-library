import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from search.shelf import load_ai_meta
from semantic_models import get_bge_engine


def build_fts_query(
    primary_terms: List[str], synonym_terms: Optional[List[str]] = None
) -> str:
    """
    Build SQLite FTS5 query strictly from topical terms (never from modifiers like '经典' or '书籍').
    Uses OR across topical terms & synonyms so BM25 naturally scores multi-term hits higher
    without zeroing out books that use synonymous terminology.
    """
    clean_primary: List[str] = []
    seen: Set[str] = set()
    for t in primary_terms:
        tc = re.sub(r'[^\w\u4e00-\u9fff]+', "", t).lower()
        if tc and tc not in seen:
            seen.add(tc)
            clean_primary.append(f'"{tc}"')

    clean_syns: List[str] = []
    if synonym_terms:
        for st in synonym_terms[:6]:
            stc = re.sub(r'[^\w\u4e00-\u9fff]+', "", st).lower()
            if len(stc) >= 2 and stc not in seen:
                seen.add(stc)
                clean_syns.append(f'"{stc}"')

    all_q = clean_primary + clean_syns
    if not all_q:
        return ""
    if len(clean_primary) >= 2:
        and_part = f"({' AND '.join(clean_primary[:3])})"
        or_part = f"({' OR '.join(all_q[:8])})"
        return f"{and_part} OR {or_part}"
    return " OR ".join(all_q[:8])


def build_allowed_ids(
    conn: Any,
    facet_clauses: List[str],
    facet_params: List[Any],
) -> Optional[Set[int]]:
    """Query pre-filtered book IDs enforcing all facet clauses including is_deleted = 0."""
    has_deleted = bool(conn.execute("SELECT 1 FROM books WHERE is_deleted = 1 LIMIT 1").fetchone())
    if len(facet_clauses) <= 1 and not has_deleted:
        return None
    allowed_rows = conn.execute(
        f"SELECT id FROM books WHERE {' AND '.join(facet_clauses)}",
        facet_params,
    ).fetchall()
    return {int(r["id"]) for r in allowed_rows}


_LIKE_COLUMNS = "(title LIKE ? OR subtitle LIKE ? OR author LIKE ? OR category LIKE ? OR tags LIKE ? OR summary LIKE ? OR isbn LIKE ?)"


def _direct_hits_in_one_scan(
    conn: Any, term_groups: List[Tuple[List[str], int]], facet_clauses: List[str], facet_params: List[Any], order_col: str
) -> List[Set[int]]:
    """For each (terms, limit) group: the first `limit` books, in `order_col` order, that contain any of its terms.

    One table scan answers every group (each LIKE scan of the books table costs ~40 ms, and a query has up to five
    groups); the per-group limits are applied while reading the ordered rows, so a rare term is never crowded out
    by a broad one.
    """
    group_conditions = ["(" + " OR ".join(_LIKE_COLUMNS for _ in terms) + ")" for terms, _ in term_groups]
    group_params = [f"%{term}%" for terms, _ in term_groups for term in terms for _ in range(7)]
    flags = ", ".join(f"{condition} AS g{index}" for index, condition in enumerate(group_conditions))
    sql = (
        f"SELECT id, {flags} FROM books WHERE ({' OR '.join(group_conditions)}) AND {' AND '.join(facet_clauses)} "
        f"ORDER BY {order_col}"
    )
    hits: List[Set[int]] = [set() for _ in term_groups]
    for row in conn.execute(sql, group_params + group_params + facet_params):
        for index, (_, limit) in enumerate(term_groups):
            if row[index + 1] and len(hits[index]) < limit:
                hits[index].add(int(row["id"]))
        if all(len(group_hits) >= limit for group_hits, (_, limit) in zip(hits, term_groups)):
            break
    return hits



def execute_dual_path_recall(
    conn: Any,
    intent: Dict[str, Any],
    facet_clauses: List[str],
    facet_params: List[Any],
    allowed_ids: Optional[Set[int]],
) -> Tuple[List[int], Dict[int, Any], Dict[int, Dict[str, Any]], Dict[int, float]]:
    """
    Executes Stage 2 (Dual-Path Recall) & Stage 3 (RRF Candidate Fusion).
    Returns (top_cand_ids, rows_by_id, ai_meta, dense_scores).
    """
    semantic_query = intent.get("semantic_query") or intent.get("raw_query") or ""
    primary_terms = intent.get("primary_terms") or []
    synonym_terms = intent.get("synonym_terms") or []
    is_pinyin = intent.get("is_pinyin", False)
    pinyin_joined = intent.get("pinyin_joined", "")
    wants_classic = intent.get("wants_classic", False)
    inferred_cats = intent.get("inferred_categories") or []

    # Path 1: bge-m3 1024-dim Multilingual Dense Vector Recall
    bge = get_bge_engine()
    dense_scores: Dict[int, float] = {}
    dense_ranks: Dict[int, int] = {}
    if bge.is_embedder_ready():
        dense_scores, dense_ranks = bge.dense_search(
            semantic_query, allowed_ids=allowed_ids, top_k=160
        )

    # Path 2A: FTS5 Sparse BM25 Recall (strictly topical terms + synonyms)
    sparse_ranks: Dict[int, int] = {}
    fts_q = build_fts_query(primary_terms, synonym_terms)
    if fts_q:
        try:
            fts_rows = conn.execute(
                """
                SELECT rowid AS book_id,
                       bm25(books_fts, 14.0, 6.0, 8.0, 2.0, 3.0, 5.0, 5.0, 3.5, 3.5, 3.5, 2.5, 1.5, 8.0) AS rank_val
                FROM books_fts
                WHERE books_fts MATCH ?
                ORDER BY rank_val ASC
                LIMIT 220
                """,
                (fts_q,),
            ).fetchall()
            r_idx = 0
            for r in fts_rows:
                bid = int(r["book_id"])
                if allowed_ids is not None and bid not in allowed_ids:
                    continue
                r_idx += 1
                sparse_ranks[bid] = r_idx
                if r_idx >= 160:
                    break
        except Exception:
            # FTS5 syntax errors on special characters fallback to like/dense recall
            logging.getLogger(__name__).debug("FTS5 query failed; relying on LIKE and dense recall", exc_info=True)

    # Path 2B: Direct SQL Topical & Pinyin Recall (guarantees 100% recall of exact title/author/ISBN/pinyin/category matches)
    direct_hit_ids: Set[int] = set()
    synonym_direct_ids: Set[int] = set()

    title_spellings = [t for t in (intent.get("explicit_title"), intent.get("explicit_title_variant")) if t and len(t) >= 2]
    for exact_title in title_spellings:
        sql_et = f"""
            SELECT id FROM books
            WHERE (title = ? OR norm_title = ? OR title LIKE ? OR norm_title LIKE ?)
              AND {' AND '.join(facet_clauses)}
            ORDER BY (CASE WHEN norm_title = ? THEN 1 WHEN title = ? THEN 2 WHEN title LIKE ? THEN 3 ELSE 4 END), classic_score DESC
            LIMIT 40
        """
        for r_et in conn.execute(
            sql_et,
            [exact_title, exact_title, f"%{exact_title}%", f"%{exact_title}%"]
            + facet_params
            + [exact_title, exact_title, f"{exact_title}%"],
        ).fetchall():
            direct_hit_ids.add(int(r_et["id"]))

    if is_pinyin and pinyin_joined:
        py_pat = f"%{pinyin_joined}%"
        py_clauses = ["pinyin_search LIKE ?", "title LIKE ?", "author LIKE ?"]
        py_params: List[Any] = [py_pat, py_pat, py_pat]
        # Handle titles with punctuation between syllables (e.g. '思考,快与慢' -> 'sikao,kuaiyuman')
        valid_parts = [p for p in synonym_terms if len(p) >= 2]
        if len(valid_parts) >= 2:
            and_sub = " AND ".join("pinyin_search LIKE ?" for _ in valid_parts[:4])
            py_clauses.append(f"({and_sub})")
            py_params.extend(f"%{p}%" for p in valid_parts[:4])
        sql_py = f"""
            SELECT id FROM books
            WHERE ({' OR '.join(py_clauses)})
              AND {' AND '.join(facet_clauses)}
            ORDER BY classic_score DESC, quality_score DESC
            LIMIT 120
        """
        for pr in conn.execute(sql_py, py_params + facet_params).fetchall():
            direct_hit_ids.add(int(pr["id"]))
    else:
        # One query per primary term so a rare anchor ('赫拉利', 3 books) is never crowded out by a
        # broad synonym ('历史与地缘政治', 1,115 books) under a shared LIMIT.
        order_col = "classic_score DESC" if wants_classic else "quality_score DESC"
        term_groups = [([pt], 80) for pt in primary_terms[:4]]
        syn_only = [st for st in synonym_terms[:6] if st not in primary_terms]
        if syn_only:
            term_groups.append((syn_only, 120))
        group_hits = _direct_hits_in_one_scan(conn, term_groups, facet_clauses, facet_params, order_col)
        for (terms, _limit), hits in zip(term_groups, group_hits):
            (synonym_direct_ids if terms is syn_only else direct_hit_ids).update(hits)

    # Recall via DeepSeek keywords: a book whose own text never says 'blockchain' can still be found
    if not is_pinyin:
        for pt in primary_terms[:4]:
            try:
                kw_rows = conn.execute(
                    "SELECT book_id AS id FROM book_ai_meta WHERE keywords LIKE ? "
                    f"AND book_id IN (SELECT id FROM books WHERE {' AND '.join(facet_clauses)}) LIMIT 60",
                    [f'%"{pt}"%'] + facet_params,
                ).fetchall()
            except Exception:
                kw_rows = []
            for kr in kw_rows:
                direct_hit_ids.add(int(kr["id"]))

    # Also recall top classics from inferred categories when user asks for "<domain> 经典"
    if wants_classic and inferred_cats:
        cat_ph = ",".join("?" for _ in inferred_cats[:2])
        cat_sql = f"""
            SELECT id FROM books
            WHERE category IN ({cat_ph})
              AND {' AND '.join(facet_clauses)}
            ORDER BY classic_score DESC
            LIMIT 80
        """
        for cr in conn.execute(cat_sql, inferred_cats[:2] + facet_params).fetchall():
            direct_hit_ids.add(int(cr["id"]))

    # Stage 3: Reciprocal Rank Fusion (RRF)
    candidate_ids = set(dense_ranks.keys()) | set(sparse_ranks.keys()) | direct_hit_ids | synonym_direct_ids
    if not candidate_ids:
        return ([], {}, {}, {})

    rrf_list: List[Tuple[int, float]] = []
    for bid in candidate_ids:
        r_d = dense_ranks.get(bid, 260)
        r_s = sparse_ranks.get(bid, 260)
        rrf = (1.15 / (30.0 + r_d)) + (1.25 / (30.0 + r_s))
        if bid in direct_hit_ids:
            rrf += 0.06
        elif bid in synonym_direct_ids:
            # Synonym-only match stays below a top sparse/dense rank (1.25/31 ~ 0.04)
            rrf += 0.02
        rrf_list.append((bid, rrf))

    rrf_list.sort(key=lambda x: x[1], reverse=True)
    top_cand_ids = [bid for bid, _ in rrf_list[:160]]

    ph_ids = ",".join("?" for _ in top_cand_ids)
    cand_rows = conn.execute(
        f"SELECT * FROM books WHERE id IN ({ph_ids}) AND is_deleted = 0", top_cand_ids
    ).fetchall()
    rows_by_id = {int(r["id"]): r for r in cand_rows}
    ai_meta = load_ai_meta(conn, top_cand_ids)

    return (top_cand_ids, rows_by_id, ai_meta, dense_scores)
