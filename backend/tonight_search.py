"""A search on the tonight page that moves on as the reader answers it.

Asking the same sentence again leaves out what was skipped under it and every book already marked want, reading or
read, then leans the next batch toward the books wanted under that sentence and away from the ones skipped. The
sentence still decides most of the order; the lean only reshuffles nearby ranks.
"""

from typing import Any, Dict, List, Set, Tuple

import numpy as np

from database import get_conn, write_tx
from search.recommend import _apply_author_diversity
from search.retrieval import _execute_unified_hybrid_retrieval
from search.types import ShelfFilters
from search_history import normalize_query
from semantic_models import get_bge_engine
from skips import SKIP_CONTEXT_SEARCH

BATCH_SIZE = 10
FIRST_POOL = 60
WIDE_POOL = 200
# How far a wanted or skipped look-alike moves a book, against a rank score that runs 1 (first) to 0 (last)
WANT_PULL = 0.3
SKIP_PUSH = 0.3
MARKED_STATUSES = ("want", "reading", "read")


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


def _marked_ids(conn: Any, book_ids: List[int]) -> Set[int]:
    if not book_ids:
        return set()
    marks = ",".join("?" for _ in book_ids)
    statuses = ",".join("?" for _ in MARKED_STATUSES)
    rows = conn.execute(
        f"SELECT id FROM books WHERE id IN ({marks}) AND reading_status IN ({statuses})",
        (*book_ids, *MARKED_STATUSES),
    ).fetchall()
    return {int(r[0]) for r in rows}


def _closest_similarity(candidate_ids: List[int], anchor_ids: Set[int]) -> Dict[int, float]:
    """For each candidate with a vector, its cosine similarity to the most alike anchor; {} without anchors."""
    engine = get_bge_engine()
    anchor_found, anchors = engine.get_book_vectors(sorted(anchor_ids))
    found, vectors = engine.get_book_vectors(candidate_ids)
    if not anchor_found or not found:
        return {}
    closest = (vectors @ anchors.T).max(axis=1)
    return {book_id: float(score) for book_id, score in zip(found, np.asarray(closest))}


def lean_by_feedback(pool: List[Dict[str, Any]], wanted: Set[int], skipped: Set[int]) -> List[Dict[str, Any]]:
    """`pool` reordered: rank score plus a pull toward wanted look-alikes and a push away from skipped ones."""
    if not pool or not (wanted or skipped):
        return pool
    ids = [int(item["id"]) for item in pool]
    pull = _closest_similarity(ids, wanted)
    push = _closest_similarity(ids, skipped)
    count = len(pool)

    def leaned(index: int) -> float:
        book_id = ids[index]
        return 1 - index / count + WANT_PULL * pull.get(book_id, 0.0) - SKIP_PUSH * push.get(book_id, 0.0)

    order = sorted(range(count), key=leaned, reverse=True)
    return [pool[index] for index in order]


def _unseen_pool(query: str, size: int) -> Tuple[List[Dict[str, Any]], int, Set[int], Set[int]]:
    """(candidates left after the exclusions, raw candidate count, wanted ids, skipped ids)."""
    raw = _execute_unified_hybrid_retrieval(
        raw_query=query, filters=ShelfFilters(), max_results=size, enable_cross_encoder=True,
    )["items"]
    conn = get_conn()
    try:
        skipped, wanted = _feedback(conn, query)
        hidden = skipped | wanted | _marked_ids(conn, [int(item["id"]) for item in raw])
    finally:
        conn.close()
    return [item for item in raw if int(item["id"]) not in hidden], len(raw), wanted, skipped


def tonight_search(raw_query: Any) -> Dict[str, Any]:
    """{items, exhausted}: the next BATCH_SIZE books for `raw_query`. `exhausted` is true when the sentence finds
    books but every one of them was already answered."""
    query = normalize_query(raw_query)
    pool, raw_count, wanted, skipped = _unseen_pool(query, FIRST_POOL)
    if len(pool) < BATCH_SIZE and raw_count >= FIRST_POOL:
        pool, raw_count, wanted, skipped = _unseen_pool(query, WIDE_POOL)
    batch = _apply_author_diversity(lean_by_feedback(pool, wanted, skipped), query, BATCH_SIZE)
    return {"query": query, "items": batch, "exhausted": raw_count > 0 and not batch}
