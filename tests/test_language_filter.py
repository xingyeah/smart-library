import tempfile
import unittest
from pathlib import Path

import database
from search.shelf import _parse_language_filter, search_empty_shelf


class TestLanguageFilter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "test_lang.db"
        database.init_db(self.db_path)

        conn = database.get_conn(self.db_path)
        conn.executemany(
            """
            INSERT INTO books (
                dedup_key, title, norm_title, author, norm_author,
                language, category, primary_format, primary_path
            ) VALUES (?, ?, ?, 'author', 'author', ?, '心理学', 'epub', '/tmp/p')
            """,
            [
                ("k1", "书1", "书1", "zh-CN"),
                ("k2", "书2", "书2", "zh-TW"),
                ("k3", "书3", "书3", "en"),
            ],
        )
        conn.commit()
        conn.close()

    def test_parse_language_filter_zh_cn(self):
        langs = _parse_language_filter("zh-CN")
        # 必须包含大写 zh-CN，且不能混入 zh-TW
        self.assertIn("zh-CN", langs)
        self.assertNotIn("zh-TW", langs)

    def test_parse_language_filter_zh_tw(self):
        langs = _parse_language_filter("zh-TW")
        self.assertIn("zh-TW", langs)
        self.assertNotIn("zh-CN", langs)

    def test_search_empty_shelf_with_zh_cn(self):
        import search.shelf as shelf_mod
        from unittest import mock

        with mock.patch.object(shelf_mod, "get_conn", lambda: database.get_conn(self.db_path)):
            res = shelf_mod.search_empty_shelf(lang_list=_parse_language_filter("zh-CN"))
            self.assertEqual(res["total"], 1)
            self.assertEqual(res["items"][0]["title"], "书1")


if __name__ == "__main__":
    unittest.main()
