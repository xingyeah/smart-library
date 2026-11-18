"""One table scan must give each term group exactly what its own LIMIT query gave."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import database  # noqa: E402
from search.recall import _direct_hits_in_one_scan  # noqa: E402

BROAD_BOOKS = 30


class DirectHitsInOneScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db_path = os.path.join(self.tmp.name, "t.db")
        database.init_db(db_path)
        self.conn = database.get_conn(db_path)
        # quality_score descends with id, so "first N in order" is ids 1..N
        for book_id in range(1, BROAD_BOOKS + 1):
            title = "赫拉利传" if book_id == BROAD_BOOKS else f"历史通论{book_id}"
            self.conn.execute(
                "INSERT INTO books (id, dedup_key, title, norm_title, primary_format, primary_path, quality_score) "
                "VALUES (?, ?, ?, ?, 'epub', '', ?)",
                (book_id, f"k{book_id}", title, title, 100 - book_id),
            )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def scan(self, groups):
        return _direct_hits_in_one_scan(self.conn, groups, ["is_deleted = 0"], [], "quality_score DESC")

    def test_each_group_keeps_its_own_limit_and_order(self):
        broad, rare = self.scan([(["历史"], 5), (["赫拉利"], 5)])
        self.assertEqual(broad, {1, 2, 3, 4, 5})
        self.assertEqual(rare, {BROAD_BOOKS})  # the broad group's limit does not crowd out the rare one

    def test_book_matching_two_groups_counts_for_both(self):
        first, second = self.scan([(["历史"], 3), (["通论"], 3)])
        self.assertEqual(first, second)

    def test_no_match_gives_empty_sets(self):
        self.assertEqual(self.scan([(["不存在的词"], 5)]), [set()])


if __name__ == "__main__":
    unittest.main()
