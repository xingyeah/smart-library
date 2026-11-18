"""Cards (book_ai_meta.clean_title) and the detail (books.title) must show the same book name."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import database  # noqa: E402
from book_enrichment_service import title_needing_confirmation  # noqa: E402
from sync_display_titles import plan_fix  # noqa: E402


class TitleSyncTriggerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "t.db")
        database.init_db(self.db_path)
        self.conn = database.get_conn(self.db_path)
        cur = self.conn.execute(
            "INSERT INTO books (dedup_key, title, norm_title, author, norm_author, primary_format, primary_path) "
            "VALUES ('k', '旧名 (Old)', '旧名', 'A', 'a', 'epub', '/tmp/x.epub')"
        )
        self.bid = cur.lastrowid
        self.conn.execute("INSERT INTO book_ai_meta (book_id, clean_title) VALUES (?, '旧名卡片')", (self.bid,))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def clean_title(self):
        return self.conn.execute("SELECT clean_title FROM book_ai_meta WHERE book_id = ?", (self.bid,)).fetchone()[0]

    def test_title_write_replaces_card_title(self):
        self.conn.execute("UPDATE books SET title = '新名 (New)' WHERE id = ?", (self.bid,))
        self.assertEqual(self.clean_title(), "新名 (New)")

    def test_other_column_write_keeps_card_title(self):
        self.conn.execute("UPDATE books SET summary = 'x' WHERE id = ?", (self.bid,))
        self.assertEqual(self.clean_title(), "旧名卡片")


class TitleProposalTests(unittest.TestCase):
    def test_marketing_removal_needs_no_confirmation(self):
        self.assertEqual(title_needing_confirmation("引力论【限时特价】", "引力论"), "")

    def test_rename_needs_confirmation(self):
        self.assertEqual(title_needing_confirmation("演说变现 (One)", "只差一场演示 (One)"), "只差一场演示 (One)")

    def test_same_or_empty_needs_no_confirmation(self):
        self.assertEqual(title_needing_confirmation("书", "书"), "")
        self.assertEqual(title_needing_confirmation("书", " "), "")


class BackfillPlanTests(unittest.TestCase):
    def test_restores_chinese_name_keeping_title_english(self):
        row = {"title": "列车科学 (Flying Trains)", "clean_title": "飞行列车 (Flying)", "author": "X"}
        self.assertEqual(plan_fix(row)["title"], "飞行列车 (Flying Trains)")

    def test_author_in_parentheses_takes_old_english(self):
        row = {"title": "法治秩序 (Jill Lepore)", "clean_title": "人造国家的兴衰 (The Rise)", "author": "Jill Lepore"}
        self.assertEqual(plan_fix(row)["title"], "人造国家的兴衰 (The Rise)")

    def test_no_chinese_in_old_name_keeps_title(self):
        row = {"title": "名人传记读本 (Who Was)", "clean_title": "Who Was Magellan?", "author": "X"}
        self.assertEqual(plan_fix(row), {"action": "reset", "title": None, "clean_title": "名人传记读本 (Who Was)"})


if __name__ == "__main__":
    unittest.main()
