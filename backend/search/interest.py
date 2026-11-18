"""
Interest recommendations: books for a reader, from the books they marked and the interest prompt they wrote.

Three kinds of pick fill the list:
  - prompt picks: the books nearest each phrase of the prompt. A phrase is a group of its own, because scored
    among the liked books it is never the nearest source of any top candidate and so would never be seen.
  - taste picks: the books nearest the liked books (search/taste.py), grouped by the category of the liked book
    each is nearest to.
  - exploration picks: classics, taken in turn from the categories the reader marked least. Their share of the
    list is the reader's exploration ratio; unless that is zero they also fill any place the first two could not.
The prompt and taste groups take turns, one pick per group per round (search/diverse_picks.py), so one topic the
reader marked many books on cannot fill the list.

Scores over the whole library are kept between requests; what may be picked (not marked, not deleted, language,
excluded categories) is read from the database on every request, so a deletion or a new mark shows at once.
"""

import itertools
import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Set, Tuple

import numpy as np

from database import get_conn
from search.filters import build_book_facet_where, parse_language_filter
from search.intent import analyze_query_intent
from search.shelf import _format_book_row, attach_ai_meta
from search.types import ShelfFilters
from search.diverse_picks import UNKNOWN_AUTHOR, CandidateGroup, pick_round_robin
from search.taste import (
    NEAREST_LIKED_BOOKS, SAME_WORK_COSINE, SourceScores, centred_unit_rows, nearest_source_scores,
)
from semantic_models import get_bge_engine

logger = logging.getLogger(__name__)

MAX_PROMPT_PHRASES = 5
MIN_PHRASE_CHARS = 2
MAX_EXPLORATION_RATIO = 0.5  # see explore_places
SHORTLIST_MIN = 300  # ranked books looked at per channel; the author and same-work rules reject many of them
SHORTLIST_PER_PICK = 5
PREFERRED_CATEGORY_BONUS = 0.03  # added to the centred-cosine score of a book in a preferred category
SQL_IN_CHUNK = 500  # values per IN (...) list, under SQLite's limit on bound parameters
DEFAULT_EXPLORATION_RATIO = 0.2
MATCH_SCORE_FLOOR, MATCH_SCORE_SPAN = 55.0, 43.0
MATCH_RELEVANCE_SHARE = 0.8  # the rest of a match score is the book's classic score
MIN_SCORE_SPREAD = 1e-6  # guards the relevance scale when the median score sits at its top
DEFAULT_CLASSIC_SCORE = 25.0
CLASSIC_SCORE_MAX = 100.0
# An exploration pick shows a match of its own, from the classic score alone, kept in a band that reads as "good"
EXPLORE_DEFAULT_CLASSIC_SCORE = 40.0
EXPLORE_MATCH_BASE, EXPLORE_MATCH_PER_CLASSIC_POINT = 60.0, 0.5
EXPLORE_MATCH_MIN, EXPLORE_MATCH_MAX = 82.0, 96.5
DEFAULT_CATEGORY = "综合通识"
UNSORTED_PHRASE_CATEGORY = "核心关注"
TASTE_SOURCE, PROMPT_SOURCE, EXPLORE_SOURCE = "阅读足迹", "兴趣罗盘定义", "破茧探索"
LIKED_SQL = "is_favorite = 1 OR user_rating >= 4 OR reading_status IN ('reading', 'read', 'want')"
MARKED_SQL = "is_favorite = 1 OR COALESCE(reading_status, 'unread') NOT IN ('unread', '') OR COALESCE(user_rating, 0) > 0"

# (ids per row, row of each id) for the index in use: building it takes 30 ms on a library ten times this one
_row_of_cache: Optional[Tuple[np.ndarray, Dict[int, int]]] = None
# (matrix, liked rows, their scores) of the last taste scoring: 0.7 s on a library ten times this one
_taste_cache: Optional[Tuple[np.ndarray, Tuple[int, ...], SourceScores]] = None


def _id_array(book_ids: Set[int]) -> np.ndarray:
    return np.fromiter(book_ids, dtype=np.int64, count=len(book_ids))


class _Pick(NamedTuple):
    row: int
    relevance: float  # 0 at the median candidate of its channel, 1 at the cosine of another copy of a liked book


@dataclass
class _Channel:
    """One ranking of the whole library and how to describe a pick made from it."""

    scores: np.ndarray
    source: str  # shown as the pick's cluster source
    # row -> (cluster name, reason) for a pick at that row; picks of one cluster take their turns as one group
    cluster_of_row: Callable[[int], Tuple[str, str]]


@dataclass
class _Group:
    """The shortlisted candidates of one cluster of a channel, in the form pick_round_robin takes."""

    channel: _Channel
    rows: List[int]
    shown_relevance: List[float]  # what the match score is made from: the candidates' relevance without bonuses
    candidates: CandidateGroup


@dataclass
class _PickSession:
    """One call of recommend_by_interests: what may be picked, and the picks so far."""

    conn: Any
    matrix: np.ndarray
    row_book_ids: np.ndarray
    blocked_ids: Set[int]  # never recommended to this reader
    allowed_ids: Optional[Set[int]]  # the language filter; None when there is none
    top_k: int
    exploration_ratio: float  # 0 to MAX_EXPLORATION_RATIO; 0 turns exploration off
    preferred: Set[str] = field(default_factory=set)
    books: List[Dict[str, Any]] = field(default_factory=list)
    picked_rows: List[int] = field(default_factory=list)
    is_candidate: np.ndarray = field(init=False)  # per matrix row: may still be picked in this session
    row_of: Dict[int, int] = field(init=False)

    def __post_init__(self) -> None:
        global _row_of_cache
        cached = _row_of_cache
        if cached is None or cached[0] is not self.row_book_ids:
            cached = _row_of_cache = (self.row_book_ids, {int(book_id): row for row, book_id
                                                           in enumerate(self.row_book_ids.tolist())})
        self.row_of = cached[1]
        self.is_candidate = ~np.isin(self.row_book_ids, _id_array(self.blocked_ids))
        if self.allowed_ids is not None:
            self.is_candidate &= np.isin(self.row_book_ids, _id_array(self.allowed_ids))

    @property
    def exploit_places(self) -> int:
        """Places for prompt and taste picks: what exploration does not take."""
        return self.top_k - explore_places(self.top_k, self.exploration_ratio)

    def block_rows(self, is_blocked: np.ndarray) -> None:
        """Takes the rows flagged in `is_blocked` out of this session for good, exploration picks included."""
        self.is_candidate &= ~is_blocked
        self.blocked_ids.update(int(book_id) for book_id in self.row_book_ids[is_blocked])

    def may_recommend(self, book_id: int) -> bool:
        """By id, so it also answers for a book that has no vector yet."""
        return book_id not in self.blocked_ids and (self.allowed_ids is None or book_id in self.allowed_ids)


def explore_places(top_k: int, exploration_ratio: float) -> int:
    """How many of `top_k` places go to exploration picks at a ratio already within 0 to MAX_EXPLORATION_RATIO:
    the ratio's share, half a place rounding up, and never more than half the list. A list of one has no half to
    give; it explores only at the top ratio."""
    if top_k == 1:
        return int(exploration_ratio >= MAX_EXPLORATION_RATIO)
    return min(math.floor(top_k * exploration_ratio + 0.5), top_k // 2)


def _valid_exploration_ratio(stored: Any) -> float:
    """The stored ratio within 0 to MAX_EXPLORATION_RATIO; 0 for a value that is no number at all."""
    try:
        ratio = float(stored)
    except (TypeError, ValueError):
        ratio = math.nan
    if not math.isfinite(ratio):
        logger.warning("user_interest_config.exploration_ratio is %r; exploring nothing", stored)
        return 0.0
    return min(MAX_EXPLORATION_RATIO, max(0.0, ratio))


def _load_config(conn: Any) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM user_interest_config WHERE id = 1").fetchone()

    def category_list(column: str) -> List[str]:
        try:
            loaded = json.loads(row[column] or "[]") if row else []
        except ValueError:
            logger.warning("user_interest_config.%s is not JSON; treating it as empty", column)
            return []
        return [str(item) for item in loaded] if isinstance(loaded, list) else []

    return {
        "interest_prompt": (row["interest_prompt"] or "").strip() if row else "",
        "preferred_categories": category_list("preferred_categories"),
        "excluded_categories": category_list("excluded_categories"),
        "exploration_ratio": _valid_exploration_ratio(row["exploration_ratio"] if row else DEFAULT_EXPLORATION_RATIO),
    }


def _load_marks(conn: Any, excluded_categories: List[str]) -> Tuple[List[Any], Set[int]]:
    """(liked, blocked): the liked books that are not deleted (id, title, category), and the ids never recommended:
    marked books and other copies with their title, deleted books, anthologies, and books of an excluded category.

    One pass over the table for the flags (a pass costs about 40 ms here whatever it selects), then index lookups
    for the titles and categories."""
    flagged = conn.execute(
        f"SELECT id, title, category, norm_title, ({MARKED_SQL}) AS is_marked, "
        f"(is_deleted = 0 AND ({LIKED_SQL})) AS is_liked "
        f"FROM books WHERE is_deleted = 1 OR is_anthology = 1 OR {MARKED_SQL} ORDER BY id").fetchall()
    blocked = {int(book["id"]) for book in flagged}
    marked_titles = sorted({book["norm_title"] for book in flagged if book["is_marked"] and book["norm_title"]})
    for column, values in (("norm_title", marked_titles), ("category", excluded_categories)):
        for start in range(0, len(values), SQL_IN_CHUNK):
            chunk = values[start:start + SQL_IN_CHUNK]
            placeholders = ",".join("?" for _ in chunk)
            blocked.update(int(row["id"]) for row in conn.execute(
                f"SELECT id FROM books WHERE {column} IN ({placeholders})", chunk))
    return [book for book in flagged if book["is_liked"]], blocked


def _language_ids(conn: Any, language: str) -> Optional[Set[int]]:
    """Ids of the non-deleted books in the languages of the comma-separated filter; None when it names none."""
    if not parse_language_filter(language):
        return None
    clauses, params = build_book_facet_where(ShelfFilters(language=language))
    return {int(row["id"]) for row in conn.execute(
        f"SELECT id FROM books WHERE {' AND '.join(clauses)}", params)}


def _taste_channel(session: _PickSession, liked: List[Any]) -> Optional[_Channel]:
    """Ranks by nearness to the liked books that have a vector; None when none has. Books so close to a liked
    one that they are the same work are blocked from the whole session."""
    global _taste_cache
    with_vector = [book for book in liked if int(book["id"]) in session.row_of]
    if not with_vector:
        return None
    liked_rows = tuple(session.row_of[int(book["id"])] for book in with_vector)
    cached = _taste_cache
    if cached is None or cached[0] is not session.matrix or cached[1] != liked_rows:
        result = nearest_source_scores(session.matrix, session.matrix[list(liked_rows)], NEAREST_LIKED_BOOKS)
        cached = _taste_cache = (session.matrix, liked_rows, result)
    taste = cached[2]
    session.block_rows(taste.peak >= SAME_WORK_COSINE)

    def cluster_of_row(row: int) -> Tuple[str, str]:
        near = with_vector[int(taste.nearest[row])]
        return f"{near['category'] or DEFAULT_CATEGORY}偏好", f"与你标记的《{near['title']}》相近"

    return _Channel(scores=taste.scores, source=TASTE_SOURCE, cluster_of_row=cluster_of_row)


def _read_prompt(prompt: str) -> Tuple[Dict[str, str], Dict[str, np.ndarray]]:
    """(category of each phrase of the prompt, vector of each phrase the embedder can encode)."""
    bge = get_bge_engine()
    categories: Dict[str, str] = {}
    vectors: Dict[str, np.ndarray] = {}
    for phrase in [part.strip() for part in re.split(r"[,，、；;。\n]+", prompt)]:
        if len(phrase) < MIN_PHRASE_CHARS or phrase in categories or len(categories) >= MAX_PROMPT_PHRASES:
            continue
        intent = analyze_query_intent(phrase)
        inferred = intent["inferred_categories"]
        categories[phrase] = inferred[0] if inferred else UNSORTED_PHRASE_CATEGORY
        vector = bge.encode_query(intent["semantic_query"])
        if vector is not None:
            vectors[phrase] = np.asarray(vector, dtype=np.float32)
    return categories, vectors


def _prompt_channels(session: _PickSession, phrase_vectors: Dict[str, np.ndarray]) -> List[_Channel]:
    return [
        _Channel(scores=nearest_source_scores(session.matrix, vector[None, :], 1).scores, source=PROMPT_SOURCE,
                 cluster_of_row=lambda row, phrase=phrase: (phrase, f"契合你写下的关注点「{phrase}」"))
        for phrase, vector in phrase_vectors.items()
    ]


def _channel_groups(session: _PickSession, channel: _Channel) -> List[_Group]:
    """The channel's best candidates split by cluster, the cluster with the best candidate first."""
    scores = np.where(session.is_candidate, channel.scores, -np.inf)
    usable = np.isfinite(scores)
    shortlist_size = min(int(usable.sum()), max(SHORTLIST_MIN, session.exploit_places * SHORTLIST_PER_PICK))
    if shortlist_size == 0:
        return []
    rows = [int(row) for row in np.argpartition(-scores, shortlist_size - 1)[:shortlist_size]]
    placeholders = ",".join("?" for _ in rows)
    # Only what the pick rules need; the full row is read for the few books that are picked
    facts = {int(record["id"]): record for record in session.conn.execute(
        f"SELECT id, category, norm_author FROM books WHERE id IN ({placeholders})",
        [int(session.row_book_ids[row]) for row in rows])}
    # 0 at the median candidate, 1 at SAME_WORK_COSINE, which no candidate reaches: a fixed top, so a prompt
    # phrase nothing in the library answers shows a low match instead of the same 90s as a good one
    median = float(np.median(scores[usable]))
    scale = max(SAME_WORK_COSINE - median, MIN_SCORE_SPREAD)
    by_cluster: Dict[str, List[int]] = {}
    for row in rows:
        if int(session.row_book_ids[row]) in facts:
            by_cluster.setdefault(channel.cluster_of_row(row)[0], []).append(row)
    groups = []
    for group_rows in by_cluster.values():
        book_ids = [int(session.row_book_ids[row]) for row in group_rows]
        relevance = np.clip((scores[group_rows] - median) / scale, 0.0, 1.0)
        # The preference only orders: it is added for the picking, above 1 if need be, and left out of the match
        is_preferred = np.array([facts[book_id]["category"] in session.preferred for book_id in book_ids])
        groups.append(_Group(channel, group_rows, [float(value) for value in relevance], CandidateGroup(
            book_ids=book_ids,
            relevance=(relevance + is_preferred * PREFERRED_CATEGORY_BONUS / scale).astype(np.float32),
            authors=[facts[book_id]["norm_author"] for book_id in book_ids],
            units=centred_unit_rows(session.matrix, group_rows))))
    return sorted(groups, key=lambda group: -float(group.candidates.relevance.max()))


def _load_books(conn: Any, book_ids: List[int]) -> List[Dict[str, Any]]:
    """The formatted book rows of `book_ids`, in that order; an id that is gone is left out."""
    placeholders = ",".join("?" for _ in book_ids)
    by_id = {int(record["id"]): _format_book_row(record) for record in conn.execute(
        f"SELECT * FROM books WHERE id IN ({placeholders})", book_ids)}
    return [by_id[book_id] for book_id in book_ids if book_id in by_id]


def _describe_pick(book: Dict[str, Any], channel: _Channel, pick: _Pick) -> None:
    cluster, reason = channel.cluster_of_row(pick.row)
    classic = float(book.get("classic_score") or DEFAULT_CLASSIC_SCORE)
    blended = MATCH_RELEVANCE_SHARE * pick.relevance + (1.0 - MATCH_RELEVANCE_SHARE) * classic / CLASSIC_SCORE_MAX
    book.update(
        interest_cluster=cluster, cluster_source=channel.source, recommend_type="exploit",
        cosine_similarity=round(float(channel.scores[pick.row]), 3),
        match_score=round(MATCH_SCORE_FLOOR + MATCH_SCORE_SPAN * blended, 1),
        recommend_reason=f"【{channel.source} · {cluster}】{reason}，经典指数 {round(classic, 1)}",
    )


def _classics_in_turns(session: _PickSession, marks_per_category: Dict[str, int]) -> List[Any]:
    """Classics that may be recommended (id, norm_author, classic_score), as many per category as the list could
    still take. The categories take turns, the one with the fewest marks first, each giving its greatest classic
    left."""
    places = session.top_k - len(session.books)
    picked_ids = {int(book["id"]) for book in session.books}
    by_category: Dict[str, List[Any]] = {}
    for (stored_category,) in session.conn.execute("SELECT DISTINCT category FROM books").fetchall():
        classics = by_category.setdefault(stored_category or DEFAULT_CATEGORY, [])
        # Walks idx_books_category_classic best first and stops early: no sort, and little of the table is read.
        # No is_deleted or is_anthology test here: those books are in blocked_ids
        for record in session.conn.execute(
                "SELECT id, norm_author, classic_score FROM books WHERE category IS ? ORDER BY classic_score DESC",
                (stored_category,)):
            if len(classics) >= places:
                break
            if int(record["id"]) not in picked_ids and session.may_recommend(int(record["id"])):
                classics.append(record)
    # Among categories with as many marks, the one holding the greater classic goes first
    least_marked_first = sorted(
        (category for category, classics in by_category.items() if classics),
        key=lambda category: (marks_per_category.get(category, 0), -(by_category[category][0]["classic_score"] or 0.0)))
    in_turns: List[Any] = []
    for one_round in itertools.zip_longest(*(by_category[category] for category in least_marked_first)):
        in_turns.extend(record for record in one_round if record is not None)
    return in_turns


def _pick_explore(session: _PickSession, marks_per_category: Dict[str, int]) -> None:
    """Fills the list up to its length with classics, unless exploration is off (a ratio of zero). One book per
    author, counting the picks already made, and never a second copy of a book in the list. A book with no
    vector yet cannot be compared with the others and is taken as different."""
    if session.exploration_ratio <= 0:
        return
    authors = {book.get("norm_author") or UNKNOWN_AUTHOR for book in session.books} - {UNKNOWN_AUTHOR}
    picked_units = list(centred_unit_rows(session.matrix, session.picked_rows))
    chosen: List[int] = []
    for record in _classics_in_turns(session, marks_per_category):
        if len(session.books) + len(chosen) >= session.top_k:
            break
        author = record["norm_author"] or UNKNOWN_AUTHOR
        row = session.row_of.get(int(record["id"]))
        unit = centred_unit_rows(session.matrix, [row])[0] if row is not None else None
        is_copy = unit is not None and bool(picked_units) and float(
            (np.stack(picked_units) @ unit).max()) >= SAME_WORK_COSINE
        if author in authors or is_copy:
            continue
        if unit is not None:
            picked_units.append(unit)
        if author != UNKNOWN_AUTHOR:
            authors.add(author)
        chosen.append(int(record["id"]))
    for book in _load_books(session.conn, chosen):
        classic = round(float(book.get("classic_score") or EXPLORE_DEFAULT_CLASSIC_SCORE), 1)
        match = EXPLORE_MATCH_BASE + classic * EXPLORE_MATCH_PER_CLASSIC_POINT
        book.update(
            interest_cluster=f"跨界经典拓展 · {book['category']}", cluster_source=EXPLORE_SOURCE,
            recommend_type="explore", match_score=round(min(EXPLORE_MATCH_MAX, max(EXPLORE_MATCH_MIN, match)), 1),
            recommend_reason=f"【跨界高分探索 · {book['category']}】跳出日常阅读茧房，馆藏经典指数 {classic} 分代表作",
        )
        session.books.append(book)


def _liked_per_category(liked: List[Any]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for book in liked:
        category = book["category"] or DEFAULT_CATEGORY
        counts[category] = counts.get(category, 0) + 1
    return counts


def _marks_per_category(liked: List[Any], phrase_categories: Dict[str, str]) -> Dict[str, int]:
    """How much the reader has shown of each category: a liked book is one mark, and so is a prompt phrase whose
    category could be told."""
    marks = _liked_per_category(liked)
    for category in phrase_categories.values():
        if category != UNSORTED_PHRASE_CATEGORY:
            marks[category] = marks.get(category, 0) + 1
    return marks


def _active_clusters(liked: List[Any], phrase_categories: Dict[str, str]) -> List[Dict[str, Any]]:
    """What the reader's interests are made of, for display: liked categories (largest first), then the phrases."""
    counts = _liked_per_category(liked)
    clusters = [{"cluster_name": f"{category}偏好（{count} 本）", "category": category, "source": TASTE_SOURCE,
                 "book_count": count}
                for category, count in sorted(counts.items(), key=lambda item: -item[1])]
    return clusters + [{"cluster_name": phrase, "category": category, "source": PROMPT_SOURCE}
                       for phrase, category in phrase_categories.items()]


def _fill_picks(session: _PickSession, liked: List[Any], phrase_vectors: Dict[str, np.ndarray]) -> None:
    """Prompt and taste picks into the places exploration leaves: the groups take turns to pick, and the picks
    are listed by match score."""
    taste = _taste_channel(session, liked)
    prompt_groups = [group for channel in _prompt_channels(session, phrase_vectors)
                     for group in _channel_groups(session, channel)]
    taste_groups = _channel_groups(session, taste) if taste else []
    # The best taste group, then the phrases, then the other taste groups: with a single place the liked books
    # have it, and every phrase the reader wrote gets its turn before the lesser taste groups
    groups = taste_groups[:1] + prompt_groups + taste_groups[1:]
    picks: Dict[int, Tuple[_Channel, _Pick]] = {}  # by book id, in pick order
    for group_index, position in pick_round_robin([group.candidates for group in groups], session.exploit_places):
        group = groups[group_index]
        row = group.rows[position]
        picks[group.candidates.book_ids[position]] = (group.channel, _Pick(row, group.shown_relevance[position]))
        session.picked_rows.append(row)
        session.is_candidate[row] = False
    books = _load_books(session.conn, list(picks))
    for book in books:
        channel, pick = picks[int(book["id"])]
        _describe_pick(book, channel, pick)
    # Turns decide which books are in; the list then runs from the best match down, so a weak pick that had an
    # early turn does not sit at the top (the sort is stable: equal matches keep their turn order)
    session.books.extend(sorted(books, key=lambda book: -book["match_score"]))


def _recommend(top_k: int, language: str, exploration_ratio: Optional[float]) -> Dict[str, Any]:
    """recommend_by_interests with the reader's exploration ratio, or with `exploration_ratio` in its place."""
    started = time.perf_counter()
    conn = get_conn()
    try:
        config = _load_config(conn)
        liked, blocked_ids = _load_marks(conn, config["excluded_categories"])
        phrase_categories, phrase_vectors = _read_prompt(config["interest_prompt"])
        row_book_ids, matrix = get_bge_engine().get_book_matrix()
        session = _PickSession(
            conn=conn, matrix=matrix, row_book_ids=row_book_ids, blocked_ids=blocked_ids,
            allowed_ids=_language_ids(conn, language), top_k=top_k, preferred=set(config["preferred_categories"]),
            exploration_ratio=config["exploration_ratio"] if exploration_ratio is None else exploration_ratio)
        if len(matrix):
            _fill_picks(session, liked, phrase_vectors)
        _pick_explore(session, _marks_per_category(liked, phrase_categories))
        for rank, book in enumerate(session.books[:top_k], start=1):
            book["rank"] = rank
        return {
            **config,
            "active_clusters": _active_clusters(liked, phrase_categories),
            "interacted_books_count": len(liked),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "items": attach_ai_meta(session.books[:top_k], conn),
        }
    finally:
        conn.close()


def recommend_by_interests(top_k: int = 10, language: str = "") -> Dict[str, Any]:
    """Up to `top_k` books for the reader (see the module docstring), each with match_score, recommend_type
    ('exploit' or 'explore') and recommend_reason, plus the interest settings and clusters for display.
    `language` is a comma-separated filter. With no vector index only exploration picks come back, and with
    the exploration ratio at zero and nothing marked or written, nothing at all."""
    return _recommend(top_k, language, None)


def interest_candidates(top_k: int = 10, language: str = "") -> Dict[str, Any]:
    """recommend_by_interests without exploration picks: only books chosen for the reader's prompt and liked
    books, for a caller that goes on to choose among them. The settings in the result are the reader's own."""
    return _recommend(top_k, language, 0.0)
