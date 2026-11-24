"""Nearest-neighbour search over the book vectors: filters, bad input, and similar books that skip hidden ones."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import database  # noqa: E402
import semantic_models  # noqa: E402
from http_api.handlers import books as books_handler  # noqa: E402

BOOK_COUNT = 12
ANGLE_STEP = 0.1  # radians between neighbours: book 1 is nearest the query axis, book 12 the farthest


def fan_of_vectors() -> np.ndarray:
    """Row i (book id i + 1) lies i * ANGLE_STEP away from the first axis, each in a plane of its own."""
    matrix = np.zeros((BOOK_COUNT, semantic_models.EMBED_DIM), dtype=np.float32)
    for i in range(BOOK_COUNT):
        matrix[i, 0], matrix[i, i + 1] = np.cos(i * ANGLE_STEP), np.sin(i * ANGLE_STEP)
    return matrix


def query_axis() -> np.ndarray:
    axis = np.zeros(semantic_models.EMBED_DIM, dtype=np.float32)
    axis[0] = 1.0
    return axis


class VectorIndexCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.engine = semantic_models.BgeM3Engine()
        self.write_index(fan_of_vectors())
        for name in ("BGE_VEC_NPY_PATH", "BGE_VEC_META_PATH"):
            patch = mock.patch.object(semantic_models, name, self.root / Path(getattr(semantic_models, name)).name)
            patch.start()
            self.addCleanup(patch.stop)

    def write_index(self, matrix: np.ndarray) -> None:
        np.save(str(self.root / Path(semantic_models.BGE_VEC_NPY_PATH).name), matrix)
        meta = {"book_ids": list(range(1, len(matrix) + 1))}
        (self.root / Path(semantic_models.BGE_VEC_META_PATH).name).write_text(json.dumps(meta), encoding="utf-8")

    def nearest_ids(self, query: np.ndarray, **filters) -> list:
        return [book_id for book_id, _ in self.engine.dense_search_by_vector(query, **filters)]


class DenseSearchByVectorTest(VectorIndexCase):
    def test_returns_the_nearest_books_in_order(self):
        hits = self.engine.dense_search_by_vector(query_axis(), top_k=3)
        self.assertEqual([book_id for book_id, _ in hits], [1, 2, 3])
        self.assertAlmostEqual(hits[1][1], float(np.cos(ANGLE_STEP)), places=5)

    def test_books_with_the_same_vector_come_back_in_index_order(self):
        matrix = fan_of_vectors()
        matrix[1:5] = matrix[1]
        self.write_index(matrix)
        for _ in range(5):
            self.assertEqual(self.nearest_ids(query_axis(), top_k=3), [1, 2, 3])

    def test_excluded_and_not_allowed_books_give_their_place_to_the_next(self):
        ids = self.nearest_ids(query_axis(), exclude_ids={1, 3}, allowed_ids={1, 2, 3, 4, 6, 7}, top_k=3)
        self.assertEqual(ids, [2, 4, 6])

    def test_fewer_books_than_asked_returns_what_there_is(self):
        self.assertEqual(self.nearest_ids(query_axis(), allowed_ids={5, 9}, top_k=30), [5, 9])
        self.assertEqual(self.nearest_ids(query_axis(), allowed_ids=set(), top_k=30), [])

    def test_query_with_nan_or_no_direction_finds_nothing(self):
        broken = query_axis()
        broken[5] = np.nan
        self.assertEqual(self.nearest_ids(broken), [])
        self.assertEqual(self.nearest_ids(np.zeros(semantic_models.EMBED_DIM, dtype=np.float32)), [])

    def test_book_with_a_broken_vector_is_never_returned(self):
        matrix = fan_of_vectors()
        matrix[0, 3] = np.nan
        self.write_index(matrix)
        hits = self.engine.dense_search_by_vector(query_axis(), top_k=BOOK_COUNT)
        self.assertEqual([book_id for book_id, _ in hits], list(range(2, BOOK_COUNT + 1)))
        self.assertFalse(any(np.isnan(score) for _, score in hits))


class HalfPublishedIndexTest(VectorIndexCase):
    """A rebuild replaces the vector file first and its meta second; a load in between must not mix them."""

    def write_stamped_meta(self, book_ids: list) -> None:
        npy = self.root / Path(semantic_models.BGE_VEC_NPY_PATH).name
        meta = {"book_ids": book_ids, "vectors_stamp": semantic_models.vector_file_stamp(npy.stat())}
        (self.root / Path(semantic_models.BGE_VEC_META_PATH).name).write_text(json.dumps(meta), encoding="utf-8")

    def test_new_vectors_with_old_meta_keep_the_old_index(self):
        self.write_stamped_meta(list(range(1, BOOK_COUNT + 1)))
        self.assertEqual(self.nearest_ids(query_axis(), top_k=1), [1])
        npy = self.root / Path(semantic_models.BGE_VEC_NPY_PATH).name
        np.save(str(npy), fan_of_vectors()[::-1])  # same count, rows now in the opposite book order
        self.assertEqual(self.nearest_ids(query_axis(), top_k=1), [1])
        self.write_stamped_meta(list(range(BOOK_COUNT, 0, -1)))
        self.assertEqual(self.nearest_ids(query_axis(), top_k=1), [1])
        self.assertEqual(self.engine.get_book_matrix()[0][0], BOOK_COUNT)

    def test_mismatched_pair_is_not_loaded_cold(self):
        self.write_stamped_meta(list(range(1, BOOK_COUNT + 1)))
        np.save(str(self.root / Path(semantic_models.BGE_VEC_NPY_PATH).name), fan_of_vectors()[::-1])
        self.assertEqual(self.nearest_ids(query_axis(), top_k=1), [])


class SimilarBooksTest(VectorIndexCase):
    """Book 1 asks for its neighbours; its two nearest are a deleted book and an anthology."""

    DELETED_ID, ANTHOLOGY_ID = 2, 3

    SIMILAR_COUNT = 6

    def setUp(self):
        super().setUp()
        db_path = self.root / "t.db"
        database.init_db(db_path)
        conn = database.get_conn(db_path)
        for book_id in range(1, BOOK_COUNT + 1):
            conn.execute(
                "INSERT INTO books (id, title, norm_title, primary_path, primary_format, dedup_key, is_deleted, "
                "is_anthology) VALUES (?, ?, ?, ?, 'epub', ?, ?, ?)",
                (book_id, f"Book {book_id}", f"book {book_id}", f"/tmp/{book_id}", f"k{book_id}",
                 int(book_id == self.DELETED_ID), int(book_id == self.ANTHOLOGY_ID)))
        conn.commit()
        conn.close()
        for patch in (mock.patch.object(books_handler, "get_conn", lambda: database.get_conn(db_path)),
                      mock.patch.object(books_handler, "get_bge_engine", lambda: self.engine)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_deleted_book_does_not_take_a_place_and_anthology_stays(self):
        similar = books_handler.get_similar_books(1, top_k=self.SIMILAR_COUNT)
        self.assertEqual([book["id"] for book in similar], [3, 4, 5, 6, 7, 8])


if __name__ == "__main__":
    unittest.main()
