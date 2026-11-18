"""Scoring every book by how close it sits to the reader's nearest liked books."""

import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from search import taste  # noqa: E402

VECTOR_DIM = 32
TOPIC_SHARE = 0.6  # cosine between two books of one topic


def topic_books(topic_axis: int, count: int, first_own_axis: int) -> np.ndarray:
    """`count` unit vectors that share `topic_axis` and each have an axis of their own."""
    books = np.zeros((count, VECTOR_DIM), dtype=np.float32)
    books[:, topic_axis] = np.sqrt(TOPIC_SHARE)
    for i in range(count):
        books[i, first_own_axis + i] = np.sqrt(1 - TOPIC_SHARE)
    return books


def two_topic_library() -> np.ndarray:
    """Rows 0-5 are psychology, rows 6-11 are cooking."""
    return np.vstack([topic_books(0, 6, first_own_axis=2), topic_books(1, 6, first_own_axis=8)])


class NearestSourceScoresTest(unittest.TestCase):
    def test_books_of_the_liked_topic_score_above_the_other_topic(self):
        matrix = two_topic_library()
        scores, nearest, _ = taste.nearest_source_scores(matrix, matrix[[0, 1, 2]], neighbours=3)
        self.assertGreater(scores[3:6].min(), scores[6:].max())
        self.assertTrue(set(nearest[3:6]) <= {0, 1, 2})

    def test_nearest_names_the_source_the_book_is_closest_to(self):
        matrix = two_topic_library()
        sources = np.vstack([matrix[0], matrix[6]])  # one psychology book, one cooking book
        nearest = taste.nearest_source_scores(matrix, sources, neighbours=1).nearest
        self.assertEqual(list(nearest[1:6]), [0] * 5)
        self.assertEqual(list(nearest[7:]), [1] * 5)

    def test_peak_is_the_cosine_to_the_nearest_source_and_one_for_the_source_itself(self):
        matrix = two_topic_library()
        peak = taste.nearest_source_scores(matrix, matrix[[0, 1, 2]], neighbours=3).peak
        self.assertAlmostEqual(float(peak[0]), 1.0, places=4)
        self.assertLess(float(peak[3]), 1.0)
        self.assertGreater(float(peak[3]), float(peak[7]))

    def test_agreement_of_several_sources_beats_one_source_equally_near(self):
        """Book 3 is as near to each of three liked psychology books as book 7 is to the one liked cooking book."""
        matrix = np.vstack([two_topic_library(), topic_books(20, 6, first_own_axis=21)])  # filler evens the mean
        result = taste.nearest_source_scores(matrix, matrix[[0, 1, 2, 6]], neighbours=3)
        self.assertAlmostEqual(float(result.peak[3]), float(result.peak[7]), places=2)
        self.assertGreater(result.scores[3], result.scores[7] + 0.05)

    def test_sources_on_the_far_side_count_as_zero_not_against_the_book(self):
        """One liked psychology book and two liked cooking books: for psychology book 3 the cooking sources have
        negative cosines, so its score is half its peak plus half of (peak + 0 + 0) / 3."""
        matrix = two_topic_library()
        result = taste.nearest_source_scores(matrix, matrix[[0, 6, 7]], neighbours=3)
        peak = float(result.peak[3])
        self.assertAlmostEqual(float(result.scores[3]), 0.5 * peak + 0.5 * peak / 3, places=5)

    def test_source_on_the_library_mean_has_no_direction_and_is_not_used(self):
        matrix = two_topic_library()
        mean = matrix.mean(axis=0)
        self.assertTrue(np.isneginf(taste.nearest_source_scores(matrix, mean[None, :], neighbours=1).scores).all())
        with_it = taste.nearest_source_scores(matrix, np.vstack([matrix[0], mean]), neighbours=1)
        alone = taste.nearest_source_scores(matrix, matrix[[0]], neighbours=1)
        np.testing.assert_allclose(with_it.scores, alone.scores, rtol=1e-5)

    def test_scoring_in_chunks_gives_the_same_scores(self):
        matrix = two_topic_library()
        whole = taste.nearest_source_scores(matrix, matrix[[0, 1, 2]], neighbours=3).scores
        with mock.patch.object(taste, "SCORE_CHUNK_ROWS", 5):
            chunked = taste.nearest_source_scores(matrix, matrix[[0, 1, 2]], neighbours=3).scores
        np.testing.assert_allclose(chunked, whole, rtol=1e-5)

    def test_fewer_sources_than_neighbours_uses_what_there_is(self):
        matrix = two_topic_library()
        scores = taste.nearest_source_scores(matrix, matrix[[0]], neighbours=3).scores
        self.assertGreater(scores[1], scores[7])

    def test_one_liked_book_of_a_niche_topic_is_not_drowned_by_many_of_another(self):
        """Three liked novels and one liked physics book: a physics book right next to it must beat a novel that is
        only loosely like the three. A plain mean over the three nearest would divide the physics match by three."""
        novels, physics = topic_books(0, 3, first_own_axis=2), topic_books(1, 1, first_own_axis=5)
        loose_novel, close_physics = np.zeros(VECTOR_DIM, dtype=np.float32), np.zeros(VECTOR_DIM, dtype=np.float32)
        loose_novel[[0, 6]] = np.sqrt(0.3), np.sqrt(0.7)
        close_physics[[1, 7]] = np.sqrt(0.9), np.sqrt(0.1)
        filler = topic_books(8, 6, first_own_axis=9)  # unrelated books, so the library mean is no one topic
        matrix = np.vstack([novels, physics, loose_novel, close_physics, filler])
        scores, nearest, _ = taste.nearest_source_scores(matrix, matrix[:4], neighbours=3)
        self.assertGreater(scores[5], scores[4])
        self.assertEqual(int(nearest[5]), 3)

    def test_book_sitting_on_the_library_mean_has_no_direction_and_scores_lowest(self):
        matrix = two_topic_library()
        matrix[5] = np.delete(matrix, 5, axis=0).mean(axis=0)  # the mean of the others is then the mean of all
        scores = taste.nearest_source_scores(matrix, matrix[[0, 1, 2]], neighbours=3).scores
        self.assertEqual(scores[5], -np.inf)

    def test_book_with_a_broken_vector_scores_lowest_and_no_score_is_nan(self):
        matrix = two_topic_library()
        matrix[4, 3] = np.nan
        scores = taste.nearest_source_scores(matrix, matrix[[0, 1, 2]], neighbours=3).scores
        self.assertFalse(np.isnan(scores).any())
        self.assertEqual(int(scores.argmin()), 4)


if __name__ == "__main__":
    unittest.main()
