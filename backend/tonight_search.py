"""A search on the tonight page that moves on as the reader answers it.

DeepSeek reads the sentence into a few search lines, the library recalls candidates for them, and DeepSeek scores
each candidate 0-10 with a one-line reason; books under KEEP_SCORE are dropped. Every verdict is kept, so asking
again only serves the next kept books, and new books are judged only when the kept ones run out.

Asking again leaves out books being read or read and books already answered under the sentence (a want or a skip
since the last reset). Books wanted elsewhere may show again. Without DeepSeek the plain hybrid ranking is used.
"""

import logging
from typing import Any, Dict, List, Set, Tuple

from database import get_conn, write_tx
from search.recommend import _apply_author_diversity
from search.retrieval import _execute_unified_hybrid_retrieval
from search.shelf import attach_ai_meta, format_book_row
from search.types import ShelfFilters
from search_history import normalize_query
from skips import SKIP_CONTEXT_SEARCH
from tonight_ai import AiUnavailable, judge_books, understand_query

logger = logging.getLogger("tonight_search")

BATCH_SIZE = 10
FIRST_POOL = 60
WIDE_POOL = 200
FINISHED_STATUSES = ("reading", "read")
KEEP_SCORE = 6
JUDGE_BATCH = 40
RECALL_PER_LINE = 60
RRF_K = 60
# Two judged rounds in a row with nothing worth keeping means the library has no more for this sentence
MAX_EMPTY_ROUNDS = 2
MAX_ROUNDS_PER_ASK = 2
AFFINITY_BONUS = 1


class TonightSearchError(ValueError):
    """The book or query of a tonight search record is not valid."""


def record_search_want(book_id: int, raw_query: Any) -> None:
    """Keeps that `book_id` was marked want from the results of `raw_query`."""
    query = normalize_query(raw_query)
    with write_tx() as conn:
        if not conn.execute("SELECT 1 FROM books WHERE id = ? AND is_deleted = 0", (book_id,)).fetchone():
            raise TonightSearchError(f"book {book_id} does not exist")
        conn.execute("INSERT INTO search_wants (book_id, query) VALUES (?, ?)", (book_id, query))


def undo_search_want(book_id: int) -> bool:
    """Deletes the latest want record of `book_id`; False when there is none."""
    with write_tx() as conn:
        row = conn.execute("SELECT MAX(id) FROM search_wants WHERE book_id = ?", (book_id,)).fetchone()
        if row[0] is None:
            return False
        conn.execute("DELETE FROM search_wants WHERE id = ?", (row[0],))
        return True


def reset_search(raw_query: Any) -> None:
    """Skips made under `raw_query` until now stop hiding books and stop steering it; nothing is deleted."""
    query = normalize_query(raw_query)
    with write_tx() as conn:
        last = conn.execute("SELECT COALESCE(MAX(id), 0) FROM book_skips").fetchone()[0]
        conn.execute(
            "INSERT INTO search_resets (query, after_skip_id) VALUES (?, ?) "
            "ON CONFLICT(query) DO UPDATE SET after_skip_id = excluded.after_skip_id",
            (query, int(last)),
        )


def _feedback(conn: Any, query: str) -> Tuple[Set[int], Set[int]]:
    """(skipped since the last reset, wanted) book ids under `query`."""
    row = conn.execute("SELECT after_skip_id FROM search_resets WHERE query = ?", (query,)).fetchone()
    after = int(row[0]) if row else 0
    skipped = conn.execute(
        "SELECT DISTINCT book_id FROM book_skips WHERE context = ? AND query = ? AND id > ?",
        (SKIP_CONTEXT_SEARCH, query, after),
    ).fetchall()
    wanted = conn.execute("SELECT DISTINCT book_id FROM search_wants WHERE query = ?", (query,)).fetchall()
    return {int(r[0]) for r in skipped}, {int(r[0]) for r in wanted}


def _finished_ids(conn: Any, book_ids: List[int]) -> Set[int]:
    if not book_ids:
        return set()
    marks = ",".join("?" for _ in book_ids)
    statuses = ",".join("?" for _ in FINISHED_STATUSES)
    rows = conn.execute(
        f"SELECT id FROM books WHERE id IN ({marks}) AND (reading_status IN ({statuses}) OR is_deleted = 1)",
        (*book_ids, *FINISHED_STATUSES),
    ).fetchall()
    return {int(r[0]) for r in rows}


def _book_rows(conn: Any, book_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    if not book_ids:
        return {}
    marks = ",".join("?" for _ in book_ids)
    rows = conn.execute(f"SELECT * FROM books WHERE id IN ({marks}) AND is_deleted = 0", book_ids).fetchall()
    return {int(book["id"]): book for book in attach_ai_meta([format_book_row(r) for r in rows], conn)}


def _ai_recall(understanding: Dict[str, Any], excluded: Set[int]) -> List[Dict[str, Any]]:
    """The JUDGE_BATCH best unjudged candidates over all search lines, merged by reciprocal rank."""
    lines = understanding["search_lines"]
    runs = [(line, ShelfFilters()) for line in lines]
    if understanding.get("categories"):
        runs.append((lines[0], ShelfFilters(category=",".join(understanding["categories"]))))
    fused: Dict[int, float] = {}
    books: Dict[int, Dict[str, Any]] = {}
    for line, filters in runs:
        found = _execute_unified_hybrid_retrieval(
            raw_query=line, filters=filters, max_results=RECALL_PER_LINE + len(excluded), enable_cross_encoder=False,
        )["items"]
        for rank, item in enumerate(found):
            book_id = int(item["id"])
            if book_id not in excluded:
                fused[book_id] = fused.get(book_id, 0.0) + 1 / (RRF_K + rank)
                books.setdefault(book_id, item)
    best = sorted(fused, key=fused.get, reverse=True)[:JUDGE_BATCH]
    return [books[book_id] for book_id in best]


def _judged(conn: Any, query: str) -> Dict[int, Dict[str, Any]]:
    rows = conn.execute(
        "SELECT book_id, score, reason, round FROM ai_query_judgments WHERE query = ?", (query,)
    ).fetchall()
    return {int(r[0]): {"score": int(r[1]), "reason": r[2], "round": int(r[3])} for r in rows}


def _empty_rounds_at_end(judged: Dict[int, Dict[str, Any]]) -> int:
    best_by_round: Dict[int, int] = {}
    for verdict in judged.values():
        best_by_round[verdict["round"]] = max(best_by_round.get(verdict["round"], 0), verdict["score"])
    count = 0
    for round_no in sorted(best_by_round, reverse=True):
        if best_by_round[round_no] >= KEEP_SCORE:
            break
        count += 1
    return count


def _ai_state(query: str) -> Dict[str, Any]:
    """What is known about `query` now: answered ids, verdicts, and the kept books still to show, best first."""
    conn = get_conn()
    try:
        skipped, wanted = _feedback(conn, query)
        judged = _judged(conn, query)
        kept_ids = [b for b, v in judged.items() if v["score"] >= KEEP_SCORE and b not in skipped | wanted]
        kept_ids = [b for b in kept_ids if b not in _finished_ids(conn, kept_ids)]
        rows = _book_rows(conn, kept_ids + sorted(wanted | skipped))
    finally:
        conn.close()
    liked = [rows[b] for b in wanted if b in rows]
    affinity = {(b.get("norm_author") or "") for b in liked} | {(b.get("category") or "") for b in liked}
    affinity.discard("")

    def order(book_id: int) -> Tuple[int, int]:
        book = rows[book_id]
        near = book.get("norm_author") in affinity or book.get("category") in affinity
        return (-(judged[book_id]["score"] + (AFFINITY_BONUS if near else 0)), judged[book_id]["round"])

    kept = [dict(rows[b], ai_score=judged[b]["score"], ai_reason=judged[b]["reason"]) for b in sorted(
        (b for b in kept_ids if b in rows), key=order)]
    feedback = {"wanted": [b.get("clean_title") or b["title"] for b in liked],
                "skipped": [rows[b].get("clean_title") or rows[b]["title"] for b in skipped if b in rows]}
    return {"skipped": skipped, "wanted": wanted, "judged": judged, "kept": kept, "feedback": feedback}


def _judge_next_round(query: str, understanding: Dict[str, Any], state: Dict[str, Any]) -> bool:
    """Judges the next unjudged candidates; False when the library has none left to offer."""
    excluded = set(state["judged"]) | state["skipped"] | state["wanted"]
    candidates = _ai_recall(understanding, excluded)
    conn = get_conn()
    try:
        finished = _finished_ids(conn, [int(b["id"]) for b in candidates])
    finally:
        conn.close()
    candidates = [b for b in candidates if int(b["id"]) not in finished]
    if not candidates:
        return False
    judge_books(query, candidates, dict(understanding, **state["feedback"]))
    return True


def _ai_batch(query: str) -> Dict[str, Any]:
    """The next batch judged by DeepSeek. Raises AiUnavailable only when there is nothing judged to fall back on."""
    conn = get_conn()
    try:
        categories = [r[0] for r in conn.execute(
            "SELECT DISTINCT category FROM books WHERE is_deleted = 0 AND category <> ''").fetchall()]
    finally:
        conn.close()
    understanding = understand_query(query, categories)
    state = _ai_state(query)
    for _ in range(MAX_ROUNDS_PER_ASK):
        if len(state["kept"]) >= BATCH_SIZE or _empty_rounds_at_end(state["judged"]) >= MAX_EMPTY_ROUNDS:
            break
        try:
            has_more = _judge_next_round(query, understanding, state)
        except AiUnavailable:
            if not state["kept"]:
                raise
            logger.warning("tonight AI judging failed; serving the books already kept for %r", query)
            break
        if not has_more:
            break
        state = _ai_state(query)
    batch = state["kept"][:BATCH_SIZE]
    return {"query": query, "items": batch, "exhausted": bool(state["judged"]) and not batch, "ai_status": ""}


def _plain_pool(query: str, size: int) -> Tuple[List[Dict[str, Any]], int]:
    """(candidates left after the exclusions, raw candidate count) from the plain hybrid ranking."""
    raw = _execute_unified_hybrid_retrieval(
        raw_query=query, filters=ShelfFilters(), max_results=size, enable_cross_encoder=True,
    )["items"]
    conn = get_conn()
    try:
        skipped, wanted = _feedback(conn, query)
        hidden = skipped | wanted | _finished_ids(conn, [int(item["id"]) for item in raw])
    finally:
        conn.close()
    return [item for item in raw if int(item["id"]) not in hidden], len(raw)


def plain_batch(query: str) -> Dict[str, Any]:
    """The next batch without DeepSeek: the hybrid ranking minus what was answered, wider when few are left."""
    pool, raw_count = _plain_pool(query, FIRST_POOL)
    if len(pool) < BATCH_SIZE and raw_count >= FIRST_POOL:
        pool, raw_count = _plain_pool(query, WIDE_POOL)
    batch = _apply_author_diversity(pool, query, BATCH_SIZE)
    return {"query": query, "items": batch, "exhausted": raw_count > 0 and not batch}


def tonight_search(raw_query: Any) -> Dict[str, Any]:
    """{query, items, exhausted, ai_status}: the next BATCH_SIZE books for `raw_query`. `ai_status` is "" when
    DeepSeek chose them, else why it could not ("nokey", "cap", "error") and the plain ranking was used."""
    query = normalize_query(raw_query)
    try:
        return _ai_batch(query)
    except AiUnavailable as error:
        return dict(plain_batch(query), ai_status=error.reason)
