"""Filling a list from several groups of candidates, one pick per group per round, without near-duplicates."""

import itertools
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from search.diverse_picks import UNKNOWN_AUTHOR, CandidateGroup, pick_round_robin  # noqa: E402

VECTOR_DIM = 64


class Library:
    """Hands out unit vectors that each get an axis of their own, so two of them are alike only as far as both
    lean on axis 0: their cosine is the product of their two leans."""

    def __init__(self) -> None:
        self.free_axes = itertools.count(1)

    def unit(self, lean_on_axis_zero: float = 0.0) -> np.ndarray:
        vector = np.zeros(VECTOR_DIM, dtype=np.float32)
        vector[0] = lean_on_axis_zero
        vector[next(self.free_axes)] = np.sqrt(1 - lean_on_axis_zero ** 2)
        return vector

    def units(self, leans) -> np.ndarray:
        return np.stack([self.unit(lean) for lean in leans])

    def group(self, relevances, first_id, **overrides) -> CandidateGroup:
        """Candidates with nothing in common, authors 'a<id>', ids counting up from `first_id`."""
        ids = list(range(first_id, first_id + len(relevances)))
        fields = dict(book_ids=ids, relevance=np.array(relevances, dtype=np.float32),
                      authors=[f"a{book_id}" for book_id in ids], units=self.units([0.0] * len(relevances)))
        fields.update(overrides)
        return CandidateGroup(**fields)


def picked_ids(groups, places):
    return [groups[group_index].book_ids[position] for group_index, position in pick_round_robin(groups, places)]


class PickRoundRobinTest(unittest.TestCase):
    def setUp(self):
        self.library = Library()
        self.group = self.library.group

    def test_each_group_gets_one_pick_per_round_best_first(self):
        groups = [self.group([0.9, 0.8, 0.7], 10), self.group([0.6, 0.5], 20), self.group([0.95, 0.1], 30)]
        self.assertEqual(picked_ids(groups, 5), [10, 20, 30, 11, 21])

    def test_group_that_runs_out_is_skipped_and_the_rest_go_on(self):
        groups = [self.group([0.9], 10), self.group([0.6, 0.5, 0.4], 20)]
        self.assertEqual(picked_ids(groups, 10), [10, 20, 21, 22])

    def test_different_book_beats_a_slightly_better_one_that_repeats_a_pick(self):
        """Books 11 and 12 follow 10: 11 is a little more relevant but half like 10, 12 is nothing like it."""
        groups = [self.group([0.9, 0.88, 0.8], 10, units=self.library.units([1.0, 0.5, 0.0]))]
        self.assertEqual(picked_ids(groups, 2), [10, 12])

    def test_another_copy_of_a_picked_book_is_never_picked_even_with_places_left(self):
        groups = [self.group([0.9, 0.89, 0.2], 10, units=self.library.units([1.0, 0.9, 0.0]))]
        self.assertEqual(picked_ids(groups, 3), [10, 12])

    def test_copy_in_another_group_is_not_picked_either(self):
        shared = self.library.unit(1.0)
        groups = [self.group([0.9], 10, units=shared[None, :]),
                  self.group([0.9, 0.3], 20, units=np.stack([shared, self.library.unit()]))]
        self.assertEqual(picked_ids(groups, 3), [10, 21])

    def test_one_book_per_author_but_unknown_authors_do_not_count_as_one(self):
        groups = [self.group([0.9, 0.8, 0.7, 0.6], 10, authors=["same", "same", None, UNKNOWN_AUTHOR])]
        self.assertEqual(picked_ids(groups, 4), [10, 12, 13])

    def test_book_listed_in_two_groups_is_picked_once(self):
        groups = [self.group([0.9], 10), self.group([0.9, 0.5], 20, book_ids=[10, 21])]
        self.assertEqual(picked_ids(groups, 3), [10, 21])

    def test_equal_candidates_come_out_by_book_id_every_time(self):
        groups = [self.group([0.5, 0.5, 0.5], 10, book_ids=[12, 10, 11])]
        self.assertEqual(picked_ids(groups, 3), [10, 11, 12])
        self.assertEqual(picked_ids(groups, 3), [10, 11, 12])

    def test_single_candidate_and_no_groups(self):
        self.assertEqual(picked_ids([self.group([0.4], 10)], 5), [10])
        self.assertEqual(pick_round_robin([], 5), [])
        self.assertEqual(pick_round_robin([self.group([0.4], 10)], 0), [])


if __name__ == "__main__":
    unittest.main()
