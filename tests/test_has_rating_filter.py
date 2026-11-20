import tempfile
import unittest
from pathlib import Path
from unittest import mock

import database
from search.shelf import search_empty_shelf
from search.types import ShelfFilters, ShelfQuery


class TestHasRatingFilter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "test_has_rating.db"
        database.init_db(self.db_path)

        conn = database.get_conn(self.db_path)
        # 书1: 只有豆瓣评分
        cur1 = conn.execute(
            """
            INSERT INTO books (
                dedup_key, title, norm_title, author, norm_author,
                douban_rating, douban_votes, category, primary_format, primary_path
            ) VALUES ('k1', '豆瓣高分书', '豆瓣高分书', '作者A', '作者A', 8.5, 500, '心理学', 'epub', '/tmp/1')
            """
        )
        self.b1_id = cur1.lastrowid

        # 书2: 无豆瓣评分，但有 Google Books 外部评分
        cur2 = conn.execute(
            """
            INSERT INTO books (
                dedup_key, title, norm_title, author, norm_author,
                douban_rating, category, primary_format, primary_path
            ) VALUES ('k2', 'Google评分书', 'Google评分书', '作者B', '作者B', 0.0, '心理学', 'epub', '/tmp/2')
            """
        )
        self.b2_id = cur2.lastrowid
        conn.commit()
        conn.close()

        database.upsert_external_meta(
            self.db_path,
            book_id=self.b2_id,
            source="google_books",
            rating=9.0,
            raw_rating=4.5,
            votes=200,
            status="success",
        )

        conn = database.get_conn(self.db_path)
        # 书3: 纯白板书，无任何外部评分
        cur3 = conn.execute(
            """
            INSERT INTO books (
                dedup_key, title, norm_title, author, norm_author,
                douban_rating, category, primary_format, primary_path
            ) VALUES ('k3', '未评分普通书', '未评分普通书', '作者C', '作者C', 0.0, '心理学', 'epub', '/tmp/3')
            """
        )
        self.b3_id = cur3.lastrowid

        # 书4: 无评分但有读者书评
        cur4 = conn.execute(
            """
            INSERT INTO books (
                dedup_key, title, norm_title, author, norm_author,
                douban_rating, category, primary_format, primary_path
            ) VALUES ('k4', '有书评书', '有书评书', '作者D', '作者D', 0.0, '心理学', 'epub', '/tmp/4')
            """
        )
        self.b4_id = cur4.lastrowid
        conn.commit()
        conn.close()

        database.upsert_external_meta(
            self.db_path,
            book_id=self.b4_id,
            source="douban",
            rating=0.0,
            raw_rating=0.0,
            votes=0,
            reviews=[{"author": "读者X", "content": "好书"}],
            status="success",
        )

    def test_search_shelf_has_rating_filter(self):
        with mock.patch("search.shelf.get_conn", lambda: database.get_conn(self.db_path)):
            # 不加过滤，应返回全部 4 本
            all_res = search_empty_shelf(ShelfQuery())
            self.assertEqual(all_res["total"], 4)

            # 加上 has_rating 过滤，只应返回书 1、书 2 和书 4
            rated_res = search_empty_shelf(ShelfQuery(filters=ShelfFilters(has_rating=True)))
            self.assertEqual(rated_res["total"], 3)
            titles = {b["title"] for b in rated_res["items"]}
            self.assertIn("豆瓣高分书", titles)
            self.assertIn("Google评分书", titles)
            self.assertIn("有书评书", titles)
            self.assertNotIn("未评分普通书", titles)

            # 验证书2附带综合分及其来源
            b2_item = next(b for b in rated_res["items"] if b["title"] == "Google评分书")
            self.assertEqual([r["source"] for r in b2_item["rating_sources"]], ["google_books"])
            self.assertIsNotNone(b2_item.get("composite_rating"))


if __name__ == "__main__":
    unittest.main()
