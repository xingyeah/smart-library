"""Search history: newest first, one row per query, capped, and deletable one by one."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import database  # noqa: E402
import search_history  # noqa: E402


class SearchHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db_path = Path(self.tmp.name) / "history.db"
        database.init_db(db_path)
        for target, factory in (
            ("search_history.write_tx", lambda: database.write_tx(db_path)),
            ("search_history.get_conn", lambda: database.get_conn(db_path)),
        ):
            patcher = mock.patch(target, factory)
            patcher.start()
            self.addCleanup(patcher.stop)

    def queries(self, limit=search_history.MAX_KEPT + 10):
        return [item["query"] for item in search_history.recent_searches(limit)]

    def test_newest_first_and_a_repeat_moves_to_the_front_once(self):
        for query in ("量子力学", "  行为   经济学 ", "宋史", "量子力学"):
            search_history.record_search(query)
        self.assertEqual(self.queries(), ["量子力学", "宋史", "行为 经济学"])

    def test_keeps_only_the_newest_rows(self):
        for index in range(search_history.MAX_KEPT + 5):
            search_history.record_search(f"q{index}")
        kept = self.queries()
        self.assertEqual(len(kept), search_history.MAX_KEPT)
        self.assertEqual(kept[0], f"q{search_history.MAX_KEPT + 4}")
        self.assertNotIn("q4", kept)
        self.assertEqual(len(search_history.recent_searches()), search_history.MAX_LISTED)

    def test_delete_removes_one_query(self):
        search_history.record_search("a")
        search_history.record_search("b")
        self.assertTrue(search_history.delete_search(" a "))
        self.assertFalse(search_history.delete_search("a"))
        self.assertEqual(self.queries(), ["b"])

    def test_rejects_empty_or_non_text(self):
        for raw in ("", "   ", None, 12):
            with self.assertRaises(search_history.SearchHistoryError):
                search_history.record_search(raw)


if __name__ == "__main__":
    unittest.main()
