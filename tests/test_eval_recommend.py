"""Offline evaluation of interest recommendations: hide some marked books, see where a scorer ranks them."""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from eval_recommend import FOLD_COUNT, EvalData, category_centroid_scorer, evaluate  # noqa: E402

BOOK_COUNT = 300
LIKED_COUNT = 20
VECTOR_DIM = 16
TASTE_SPREAD = 0.05  # liked books sit this close to the taste axis
ABOVE_ANY_COSINE = 10.0


def library_with_one_taste() -> EvalData:
    """300 unit vectors; the 20 liked books all point close to the first axis, the rest point anywhere."""
    rng = np.random.default_rng(seed=7)
    matrix = rng.normal(size=(BOOK_COUNT, VECTOR_DIM)).astype(np.float32)
    matrix[:LIKED_COUNT] = rng.normal(scale=TASTE_SPREAD, size=(LIKED_COUNT, VECTOR_DIM))
    matrix[:LIKED_COUNT, 0] = 1.0
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
    liked = np.arange(LIKED_COUNT)
    return EvalData(matrix=matrix, liked_rows=liked, liked_categories=["taste"] * LIKED_COUNT, marked_rows=liked)


def towards_liked_mean(matrix, train_rows, train_categories):
    return matrix @ matrix[train_rows].mean(axis=0)


def away_from_liked_mean(matrix, train_rows, train_categories):
    return -towards_liked_mean(matrix, train_rows, train_categories)


class EvaluateTest(unittest.TestCase):
    def test_scorer_that_finds_hidden_books_scores_high_and_its_opposite_low(self):
        good = evaluate(library_with_one_taste(), towards_liked_mean)
        self.assertEqual(good["recall_at_100"], 1.0)
        self.assertLess(good["median_rank"], LIKED_COUNT)
        bad = evaluate(library_with_one_taste(), away_from_liked_mean)
        self.assertEqual(bad["recall_at_100"], 0.0)

    def test_same_seeds_give_the_same_metrics(self):
        data = library_with_one_taste()
        self.assertEqual(evaluate(data, towards_liked_mean, seeds=(3, 4)), evaluate(data, towards_liked_mean, seeds=(3, 4)))

    def test_marked_books_that_are_not_hidden_are_not_candidates(self):
        def profile_books_first(matrix, train_rows, train_categories):
            scores = towards_liked_mean(matrix, train_rows, train_categories)
            scores[train_rows] = ABOVE_ANY_COSINE  # ranked, these would sit above every hidden book
            return scores

        hidden_per_fold = LIKED_COUNT / FOLD_COUNT
        self.assertLess(evaluate(library_with_one_taste(), profile_books_first)["median_rank"], hidden_per_fold)

    def test_scorer_that_cannot_tell_books_apart_finds_nothing(self):
        result = evaluate(library_with_one_taste(), lambda matrix, rows, categories: np.zeros(len(matrix)))
        self.assertEqual(result["recall_at_100"], 0.0)

    def test_rejects_nan_scores(self):
        with self.assertRaises(ValueError):
            evaluate(library_with_one_taste(), lambda matrix, rows, categories: np.full(len(matrix), np.nan))

    def test_rejects_too_few_liked_books(self):
        data = library_with_one_taste()
        few = EvalData(data.matrix, data.liked_rows[:5], data.liked_categories[:5], data.marked_rows[:5])
        with self.assertRaises(ValueError):
            evaluate(few, towards_liked_mean)

    def test_counts_every_liked_book_when_copies_are_not_asked_about(self):
        self.assertEqual(evaluate(library_with_one_taste(), towards_liked_mean)["evaluated_books"], LIKED_COUNT)

    def test_hidden_book_that_is_another_copy_of_a_liked_one_is_not_a_target(self):
        """Books 0 and 1 are one work twice: whichever is hidden, the other is still liked, so finding it again
        says nothing about taste. The other 18 liked books stay targets."""
        data = library_with_one_taste()
        data.matrix[1] = data.matrix[0]
        data.same_work_cosine = 0.99
        self.assertEqual(evaluate(data, towards_liked_mean)["evaluated_books"], LIKED_COUNT - 2)


class CategoryCentroidScorerTest(unittest.TestCase):
    def test_finds_hidden_books_of_two_opposite_tastes(self):
        """One mean over both tastes would be near zero; a centroid per category keeps each taste."""
        data = library_with_one_taste()
        half = LIKED_COUNT // 2
        data.matrix[half:LIKED_COUNT, 0] *= -1.0
        data.liked_categories = ["east"] * half + ["west"] * half
        self.assertEqual(evaluate(data, category_centroid_scorer)["recall_at_100"], 1.0)
        self.assertLess(evaluate(data, towards_liked_mean)["recall_at_100"], 1.0)


if __name__ == "__main__":
    unittest.main()
