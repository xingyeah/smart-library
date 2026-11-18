"""The one score shown per book, combined from every external rating."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from ratings import composite_rating  # noqa: E402


class CompositeRatingTest(unittest.TestCase):
    def test_few_votes_stay_near_the_prior(self):
        self.assertEqual(composite_rating([{"rating": 10.0, "votes": 1}]), 7.7)
        self.assertEqual(composite_rating([{"rating": 6.2, "votes": 11}]), 6.8)

    def test_many_votes_keep_their_rating(self):
        self.assertEqual(composite_rating([{"rating": 9.0, "votes": 20000}]), 9.0)

    def test_sources_are_weighted_by_votes(self):
        both = [{"source": "douban", "rating": 8.0, "votes": 990}, {"source": "google_books", "rating": 10.0, "votes": 10}]
        self.assertEqual(composite_rating(both), 8.0)

    def test_no_rating_above_zero_gives_none(self):
        self.assertIsNone(composite_rating([]))
        self.assertIsNone(composite_rating([{"rating": 0.0, "votes": 0}]))


if __name__ == "__main__":
    unittest.main()
