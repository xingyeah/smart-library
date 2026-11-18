"""
Fills a list from several groups of candidates so that no one group and no one topic takes it over.

Groups take turns, one pick per group per round. Inside its turn a group gives the candidate with the best
relevance after a penalty for likeness to what is already picked (maximal marginal relevance), so a second book on
a topic must be clearly better than a first book on another. Two rules are absolute: one book per known author,
and never a book that is another copy of a picked one.
"""

from dataclasses import dataclass
from typing import List, Optional, Sequence, Set, Tuple

import numpy as np

from search.taste import SAME_WORK_COSINE

# The rest weighs likeness to the picks so far. The value is the plan's and has not been tuned: the offline
# evaluation scores rankings, not the variety of a list, so it cannot choose it
RELEVANCE_WEIGHT = 0.72
UNKNOWN_AUTHOR = "未知作者"


@dataclass
class CandidateGroup:
    """Candidates that share a reason to be recommended. Position i of every field is one candidate.
    relevance: larger is better, comparable inside the group and on the scale of a cosine (0 to about 1).
    units: (n, D) unit vectors whose dot product is the likeness of two books, across groups too."""

    book_ids: List[int]
    relevance: np.ndarray
    authors: List[Optional[str]]
    units: np.ndarray


@dataclass
class _GroupState:
    group: CandidateGroup
    # Per candidate: the greatest dot product with any pick so far, and never below 0: being unlike the picks is
    # no merit, only being like them counts against a book
    likeness_to_picks: np.ndarray
    has_candidates: bool = True


def _best_position(state: _GroupState, picked_ids: Set[int], picked_authors: Set[str]) -> Optional[int]:
    """Position of the group's best candidate that may still be picked; None when there is none."""
    group = state.group
    best: Optional[Tuple[float, int, int]] = None  # (-marginal relevance, book id, position): the smallest wins
    for position, book_id in enumerate(group.book_ids):
        author = group.authors[position] or UNKNOWN_AUTHOR
        if book_id in picked_ids or (author != UNKNOWN_AUTHOR and author in picked_authors):
            continue
        likeness = float(state.likeness_to_picks[position])
        if likeness >= SAME_WORK_COSINE:
            continue
        marginal = RELEVANCE_WEIGHT * float(group.relevance[position]) - (1.0 - RELEVANCE_WEIGHT) * likeness
        key = (-marginal, int(book_id), position)
        if best is None or key < best:
            best = key
    return None if best is None else best[2]


def pick_round_robin(groups: Sequence[CandidateGroup], places: int) -> List[Tuple[int, int]]:
    """Up to `places` picks as (group index, position in that group), in pick order: groups take turns in the
    order given, and a group with nothing left to give drops out. Fewer than `places` come back when the groups
    run out; equal candidates are taken by book id, so the same input always gives the same list."""
    states = [_GroupState(group, np.zeros(len(group.book_ids), dtype=np.float32)) for group in groups]
    picks: List[Tuple[int, int]] = []
    picked_ids: Set[int] = set()
    picked_authors: Set[str] = set()
    while len(picks) < places and any(state.has_candidates for state in states):
        for group_index, state in enumerate(states):
            if len(picks) >= places or not state.has_candidates:
                continue
            position = _best_position(state, picked_ids, picked_authors)
            if position is None:
                state.has_candidates = False
                continue
            picks.append((group_index, position))
            picked_ids.add(state.group.book_ids[position])
            picked_authors.add(state.group.authors[position] or UNKNOWN_AUTHOR)
            picked_unit = state.group.units[position]
            for other in states:  # one matrix-vector product per group keeps every later turn cheap
                np.maximum(other.likeness_to_picks, other.group.units @ picked_unit, out=other.likeness_to_picks)
    return picks
