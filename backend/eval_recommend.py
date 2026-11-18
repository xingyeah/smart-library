"""
Offline evaluation of interest recommendations.

The books the reader marked (favourite, reading, read, want, rated 4+) are split into folds. Each fold in turn is
hidden, a scorer builds its picture of the reader from the other folds, and every unmarked book plus the hidden
ones is ranked by that scorer. A scorer that understands the reader ranks the hidden books near the top.

Metrics, averaged over the seeds:
  recall_at_100  share of hidden books ranked in the top 100
  recall_at_500  share ranked in the top 500
  median_rank    median 0-based rank of a hidden book

A scorer is `scorer(matrix, train_rows, train_categories) -> scores`, one score per row of the matrix.

The hidden books of a fold are ranked together, so they compete with each other for the top places (about 67 of
them per fold on today's library); a scorer that finds all of them still cannot put more than 100 in the top 100.

It measures whether marked books can be found again, not whether the reader would like the picks: read a real
recommendation list as well before trusting a change.

Usage:
  .venv/bin/python backend/eval_recommend.py
  .venv/bin/python backend/eval_recommend.py --save tests/search_eval/recommend_baseline.json
  .venv/bin/python backend/eval_recommend.py --compare tests/search_eval/recommend_baseline.json
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

# What builds the reader's profile and what is never recommended: the recommender's own definitions
from search.interest import LIKED_SQL, MARKED_SQL  # noqa: E402
from search.taste import NEAREST_LIKED_BOOKS, SAME_WORK_COSINE, nearest_source_scores  # noqa: E402

FOLD_COUNT = 5
DEFAULT_SEEDS = (0, 1, 2)
MIN_LIKED_BOOKS = FOLD_COUNT * 2  # fewer leaves a fold with nothing to learn from
RECALL_CUTOFFS = (100, 500)
METRIC_DECIMALS = 4
ZERO_NORM = 1e-12
Scorer = Callable[[np.ndarray, np.ndarray, List[str]], np.ndarray]


@dataclass
class EvalData:
    """matrix: (N, D) unit vectors. liked_rows: rows the profile is built from, with their categories.
    marked_rows: rows never offered as candidates unless hidden for the fold (a superset of liked_rows).
    same_work_cosine: when set, a book at least this close (centred cosine) to a book still liked in the fold is
    another copy of it: neither a candidate nor a target, whatever the scorer. Without it a scorer is rewarded for
    finding the other edition of a book the reader already has, which the recommender never offers."""

    matrix: np.ndarray
    liked_rows: np.ndarray
    liked_categories: List[str]
    marked_rows: np.ndarray
    same_work_cosine: Optional[float] = None


def _hidden_ranks(data: EvalData, scorer: Scorer, hidden: np.ndarray) -> List[int]:
    """Rank of each hidden book that is a target among the candidates, the best candidate being rank 0."""
    is_hidden = np.zeros(len(data.liked_rows), dtype=bool)
    is_hidden[hidden] = True
    train_rows = data.liked_rows[~is_hidden]
    train_categories = [category for category, hide in zip(data.liked_categories, is_hidden) if not hide]
    scores = np.asarray(scorer(data.matrix, train_rows, train_categories), dtype=np.float64)
    if scores.shape != (len(data.matrix),) or np.isnan(scores).any():
        raise ValueError(f"scorer must return {len(data.matrix)} scores without NaN, got shape {scores.shape}")
    is_candidate = np.ones(len(data.matrix), dtype=bool)
    is_candidate[data.marked_rows] = False
    hidden_rows = data.liked_rows[is_hidden]
    is_candidate[hidden_rows] = True
    if data.same_work_cosine is not None:
        is_candidate &= nearest_source_scores(data.matrix, data.matrix[train_rows], 1).peak < data.same_work_cosine
    candidate_scores = scores[is_candidate]
    # A tie counts against the hidden book: a scorer that gives every book one score must not look good
    return [int((candidate_scores >= scores[row]).sum()) - 1 for row in hidden_rows if is_candidate[row]]


def evaluate(data: EvalData, scorer: Scorer, seeds: Sequence[int] = DEFAULT_SEEDS) -> Dict[str, float]:
    """Metrics of `scorer` on `data` (see the module docstring), averaged over one FOLD_COUNT-fold split per seed.
    `evaluated_books` is how many liked books were targets (all of them unless copies are set aside).
    ValueError when fewer than MIN_LIKED_BOOKS books are liked, none of them is a target, or the scorer returns a
    wrong shape or NaN."""
    if len(data.liked_rows) < MIN_LIKED_BOOKS:
        raise ValueError(f"need at least {MIN_LIKED_BOOKS} liked books to evaluate, got {len(data.liked_rows)}")
    per_seed: List[Dict[str, float]] = []
    for seed in seeds:
        order = np.random.default_rng(seed).permutation(len(data.liked_rows))
        ranks = np.array([rank for fold in np.array_split(order, FOLD_COUNT)
                          for rank in _hidden_ranks(data, scorer, fold)])
        if len(ranks) == 0:
            raise ValueError(f"all {len(data.liked_rows)} liked books are copies of each other; nothing to evaluate")
        metrics = {f"recall_at_{cutoff}": float((ranks < cutoff).mean()) for cutoff in RECALL_CUTOFFS}
        metrics["median_rank"] = float(np.median(ranks))
        metrics["evaluated_books"] = float(len(ranks))
        per_seed.append(metrics)
    return {name: round(float(np.mean([metrics[name] for metrics in per_seed])), METRIC_DECIMALS)
            for name in per_seed[0]}


def category_centroid_scorer(matrix: np.ndarray, train_rows: np.ndarray, train_categories: List[str]) -> np.ndarray:
    """The scoring at the heart of recommend_by_interests today: the best cosine to any per-category mean of the
    liked books. Leaves out its classic-score term, interest-prompt clusters and diversity rules, and uses every
    liked book where it takes the 80 updated last, so this is the centroid idea at its best."""
    centroids = []
    categories = sorted(set(train_categories))
    for category in categories:
        rows = train_rows[[other == category for other in train_categories]]
        mean = matrix[rows].mean(axis=0)
        norm = float(np.linalg.norm(mean))
        if norm > ZERO_NORM:
            centroids.append(mean / norm)
    if not centroids:
        raise ValueError(f"no usable centroid from {len(train_rows)} books in {len(categories)} categories")
    return (matrix @ np.stack(centroids).T).max(axis=1)


def _indexed_rows(conn: Any, sql: str, row_of: Dict[int, int]) -> List[Tuple[int, str]]:
    """(matrix row, category) of each book `sql` selects (columns id, category) that has a vector."""
    return [(row_of[int(book["id"])], book["category"] or "") for book in conn.execute(sql) if int(book["id"]) in row_of]


def load_library_data() -> Tuple[EvalData, int]:
    """The live library, read only: the bge-m3 matrix and the reader's marks, and how many liked books have no
    vector yet (they cannot be evaluated). Never a candidate: marked books and other copies with the same title,
    as in recommend_by_interests, and deleted books and anthologies."""
    # Imported here so the tests, which bring their own vectors, do not load the embedding model
    from database import get_conn
    from semantic_models import get_bge_engine

    bge = get_bge_engine()
    book_ids, matrix = bge.get_book_matrix()
    if len(book_ids) == 0:
        raise RuntimeError("vector index not found; build it before evaluating")
    row_of = {int(book_id): row for row, book_id in enumerate(book_ids)}
    conn = get_conn()
    try:
        liked_sql = f"SELECT id, category FROM books WHERE is_deleted = 0 AND ({LIKED_SQL}) ORDER BY id"
        liked = _indexed_rows(conn, liked_sql, row_of)
        liked_total = conn.execute(f"SELECT COUNT(*) FROM ({liked_sql})").fetchone()[0]
        never_offered = _indexed_rows(conn, (
            f"SELECT id, category FROM books WHERE is_deleted = 1 OR is_anthology = 1 OR {MARKED_SQL} OR norm_title IN "
            f"(SELECT norm_title FROM books WHERE ({MARKED_SQL}) AND COALESCE(norm_title, '') != '')"), row_of)
    finally:
        conn.close()
    data = EvalData(
        matrix=matrix,
        liked_rows=np.array([row for row, _ in liked], dtype=np.int64),
        liked_categories=[category for _, category in liked],
        marked_rows=np.array(sorted({row for row, _ in never_offered}), dtype=np.int64),
        same_work_cosine=SAME_WORK_COSINE,
    )
    return data, liked_total - len(liked)


def nearest_liked_scorer(matrix: np.ndarray, train_rows: np.ndarray, train_categories: List[str]) -> np.ndarray:
    """The scoring phase 2 of 推荐算法重构实施计划.md moves recommend_by_interests to: the mean centred cosine to
    the nearest liked books (search/taste.py)."""
    return nearest_source_scores(matrix, matrix[train_rows], NEAREST_LIKED_BOOKS).scores


SCORERS: Dict[str, Scorer] = {"category_centroid": category_centroid_scorer, "nearest_liked": nearest_liked_scorer}


def run() -> Dict[str, Any]:
    """Metrics of every scorer in SCORERS on the live library."""
    data, liked_without_vector = load_library_data()
    return {
        "liked_books": int(len(data.liked_rows)),
        "liked_books_without_vector": liked_without_vector,
        "unmarked_candidates": int(len(data.matrix) - len(data.marked_rows)),
        "scorers": {name: evaluate(data, scorer) for name, scorer in SCORERS.items()},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--save", help="write the metrics as JSON (use as baseline)")
    ap.add_argument("--compare", help="baseline JSON to print next to the metrics")
    args = ap.parse_args()
    baseline = json.loads(Path(args.compare).read_text(encoding="utf-8")) if args.compare else {}
    report = run()
    print(f"{report['liked_books']} liked books ({report['liked_books_without_vector']} more have no vector), "
          f"{report['unmarked_candidates']} unmarked candidates")
    for name, metrics in report["scorers"].items():
        line = f"{name}: " + ", ".join(f"{metric}={value}" for metric, value in metrics.items())
        base = baseline.get("scorers", {}).get(name)
        print(line + (f"   (baseline {base})" if base else ""))
    if args.save:
        # Metrics only: the saved baseline is committed, book titles from the private library are not
        Path(args.save).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
