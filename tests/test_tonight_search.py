"""Tonight search: a repeated sentence leaves out what was answered and leans toward what was wanted."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import database  # noqa: E402
import skips  # noqa: E402
import tonight_search  # noqa: E402

QUERY = "宋朝 历史"


class FakeEngine:
    """Book n points along axis n % 3, so books with the same remainder are look-alikes."""

    def get_book_vectors(self, book_ids):
        vectors = np.zeros((len(book_ids), 3), dtype=np.float32)
        for row, book_id in enumerate(book_ids):
            vectors[row, int(book_id) % 3] = 1.0
        return [int(b) for b in book_ids], vectors


class TonightSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "tonight_search.db"
        database.init_db(self.db_path)
        self.pool_sizes = []
        self.found_ids = list(range(1, 31))
        patches = {
            "tonight_search.write_tx": lambda: database.write_tx(self.db_path),
            "tonight_search.get_conn": lambda: database.get_conn(self.db_path),
            "skips.write_tx": lambda: database.write_tx(self.db_path),
            "tonight_search.get_bge_engine": FakeEngine,
            "tonight_search._execute_unified_hybrid_retrieval": self.fake_retrieval,
        }
        for target, replacement in patches.items():
            patcher = mock.patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        for book_id in self.found_ids:
            self.add_book(book_id)

    def fake_retrieval(self, raw_query, filters, max_results, enable_cross_encoder):
        self.pool_sizes.append(max_results)
        return {"items": [{"id": b, "norm_author": f"author{b}"} for b in self.found_ids[:max_results]]}

    def add_book(self, book_id, status="unread"):
        conn = database.get_conn(self.db_path)
        conn.execute(
            "INSERT INTO books (id, dedup_key, title, norm_title, author, norm_author, primary_format, primary_path, "
            "reading_status) VALUES (?, ?, ?, ?, 'a', 'a', 'epub', ?, ?)",
            (book_id, f"k{book_id}", f"书{book_id}", f"书{book_id}", f"/tmp/{book_id}", status),
        )
        conn.commit()
        conn.close()

    def batch_ids(self, query=QUERY):
        return [item["id"] for item in tonight_search.tonight_search(query)["items"]]

    def test_first_ask_is_the_plain_ranking(self):
        self.assertEqual(self.batch_ids(), list(range(1, 11)))

    def test_leaves_out_skipped_under_the_same_sentence_wanted_and_marked_books(self):
        skips.record_skip(1, skips.SKIP_CONTEXT_SEARCH, "宋朝   历史 ")
        skips.record_skip(2, skips.SKIP_CONTEXT_SEARCH, "别的句子")
        tonight_search.record_search_want(3, QUERY)
        conn = database.get_conn(self.db_path)
        conn.execute("UPDATE books SET reading_status = 'read' WHERE id = 4")
        conn.commit()
        conn.close()
        batch = self.batch_ids()
        self.assertNotIn(1, batch)
        self.assertNotIn(3, batch)
        self.assertNotIn(4, batch)
        self.assertIn(2, batch)

    def test_leans_toward_wanted_and_away_from_skipped_look_alikes(self):
        tonight_search.record_search_want(3, QUERY)
        skips.record_skip(1, skips.SKIP_CONTEXT_SEARCH, QUERY)
        batch = self.batch_ids()
        # Remainder 0 books look like the wanted book 3, remainder 1 books like the skipped book 1
        self.assertEqual(batch[0], 6)
        self.assertLess(batch.index(9), batch.index(5))
        self.assertTrue(all(book % 3 != 1 for book in batch[:3]))

    def test_reset_brings_skipped_books_back_but_not_wanted_ones(self):
        skips.record_skip(1, skips.SKIP_CONTEXT_SEARCH, QUERY)
        tonight_search.record_search_want(2, QUERY)
        tonight_search.reset_search(QUERY)
        batch = self.batch_ids()
        self.assertIn(1, batch)
        self.assertNotIn(2, batch)
        skips.record_skip(5, skips.SKIP_CONTEXT_SEARCH, QUERY)
        self.assertNotIn(5, self.batch_ids())

    def test_widens_the_pool_when_few_unseen_books_are_left(self):
        self.found_ids = list(range(1, 81))
        for book_id in range(31, 81):
            self.add_book(book_id)
        for book_id in range(1, 56):
            skips.record_skip(book_id, skips.SKIP_CONTEXT_SEARCH, QUERY)
        self.assertEqual(len(self.batch_ids()), tonight_search.BATCH_SIZE)
        self.assertEqual(self.pool_sizes, [tonight_search.FIRST_POOL, tonight_search.WIDE_POOL])

    def test_exhausted_when_every_found_book_was_answered(self):
        for book_id in self.found_ids:
            skips.record_skip(book_id, skips.SKIP_CONTEXT_SEARCH, QUERY)
        result = tonight_search.tonight_search(QUERY)
        self.assertEqual(result["items"], [])
        self.assertTrue(result["exhausted"])

    def test_undo_want_lets_the_book_back(self):
        tonight_search.record_search_want(1, QUERY)
        self.assertTrue(tonight_search.undo_search_want(1))
        self.assertFalse(tonight_search.undo_search_want(1))
        self.assertIn(1, self.batch_ids())

    def test_want_of_a_missing_book_is_refused(self):
        with self.assertRaises(tonight_search.TonightSearchError):
            tonight_search.record_search_want(999, QUERY)


if __name__ == "__main__":
    unittest.main()
