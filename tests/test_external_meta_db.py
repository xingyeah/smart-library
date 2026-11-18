import sqlite3
import tempfile
import unittest
from pathlib import Path

import database


class TestExternalMetaDB(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "test_ext_meta.db"
        database.init_db(self.db_path)

        conn = database.get_conn(self.db_path)
        cur = conn.execute(
            """
            INSERT INTO books (dedup_key, title, norm_title, author, norm_author)
            VALUES ('test::1', '重塑回路', '重塑回路', 'Nicole Vignola', 'nicole vignola')
            """
        )
        self.book_id = cur.lastrowid
        conn.commit()
        conn.close()

    def test_book_external_meta_table_exists(self):
        conn = database.get_conn(self.db_path)
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='book_external_meta'"
        )
        row = cur.fetchone()
        conn.close()
        self.assertIsNotNone(row, "book_external_meta table should be created by init_db")

    def test_upsert_and_fetch_external_meta(self):
        from database import upsert_external_meta, get_external_meta_by_book

        upsert_external_meta(
            self.db_path,
            book_id=self.book_id,
            source="douban",
            external_id="36891234",
            rating=8.7,
            raw_rating=8.7,
            votes=1250,
            description="一本关于大脑可塑性的权威指南。",
            reviews=[
                {"author": "读者A", "rating": 5, "content": "写得很好，通俗易懂", "source": "douban"}
            ],
            status="success",
        )

        # 插入第二渠道
        upsert_external_meta(
            self.db_path,
            book_id=self.book_id,
            source="google_books",
            external_id="g_vol_999",
            rating=9.0,
            raw_rating=4.5,
            votes=320,
            description="Official publisher description for Rewire.",
            reviews=[],
            status="success",
        )

        items = get_external_meta_by_book(self.db_path, self.book_id)
        self.assertEqual(len(items), 2)
        sources = {it["source"]: it for it in items}
        self.assertIn("douban", sources)
        self.assertIn("google_books", sources)
        self.assertEqual(sources["douban"]["votes"], 1250)
        self.assertEqual(len(sources["douban"]["reviews"]), 1)
        self.assertEqual(sources["google_books"]["raw_rating"], 4.5)

        # 更新已存在的渠道（upsert）
        upsert_external_meta(
            self.db_path,
            book_id=self.book_id,
            source="douban",
            external_id="36891234",
            rating=8.8,
            raw_rating=8.8,
            votes=1300,
            description="更新后的简介",
            reviews=[],
            status="success",
        )
        items_updated = get_external_meta_by_book(self.db_path, self.book_id)
        self.assertEqual(len(items_updated), 2)
        douban_updated = [it for it in items_updated if it["source"] == "douban"][0]
        self.assertEqual(douban_updated["rating"], 8.8)
        self.assertEqual(douban_updated["votes"], 1300)


if __name__ == "__main__":
    unittest.main()
