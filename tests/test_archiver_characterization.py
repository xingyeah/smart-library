import os
import sys
import tempfile
import unittest
from pathlib import Path

# Add backend directory to path
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import archiver
from database import get_conn, init_db


class TestArchiverCharacterization(unittest.TestCase):
    """
    阶段零补充特征测试：锁定 archiver.upsert_enriched_book 的核心分支行为。
    包括：新书入库、同文件再次导入、同书另一格式、同名不同书、缺作者进冲突收件箱。
    """

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_library.db"
        init_db(self.db_path)
        self.conn = get_conn(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmp_dir.cleanup()

    def _make_book_item(
        self,
        title: str = "思考，快与慢",
        author: str = "丹尼尔·卡尼曼",
        fmt: str = "epub",
        file_path: str = "/tmp/mock/thinking_fast_and_slow.epub",
        file_hash: str = "hash_1111111111111111",
        file_size: int = 1024 * 1024,
        quality_score: float = 85.0,
        norm_title: str = "",
        norm_author: str = "",
    ) -> dict:
        nt = norm_title or title.lower().replace("，", "").replace(" ", "")
        na = norm_author or author.lower().replace("·", "").replace(" ", "")
        dedup_key = f"{nt}::{na}"
        return {
            "title": title,
            "subtitle": "",
            "norm_title": nt,
            "author": author,
            "norm_author": na,
            "translator": "胡晓娇",
            "publisher": "中信出版社",
            "pub_year": "2012",
            "isbn": "9787508635859",
            "douban_id": "10786473",
            "series": "",
            "volume_part": "",
            "edition_tag": "",
            "category": "心理学与脑科学",
            "tags": ["心理学", "思维", "决策"],
            "language": "zh-CN",
            "page_count": 420,
            "format": fmt,
            "file_path": file_path,
            "file_hash": file_hash,
            "file_size": file_size,
            "source_name": "Inbox",
            "cover_path": "",
            "summary": "人类的思考有两种模式：系统一和系统二……",
            "summary_source": "rule",
            "core_points": ["快思考直觉驱动", "慢思考理性严谨"],
            "target_audience": "所有人",
            "hyde_queries": ["系统一系统二", "启发式偏见"],
            "toc_text": "第一部分 两个系统 | 第二部分 启发法与偏见",
            "quality_score": quality_score,
            "pinyin_search": "si kao kuai yu man",
            "dedup_key": dedup_key,
        }

    def test_case1_new_book_inserted(self):
        """场景 1：新书正常录入，创建 books 记录与主格式 book_files 记录"""
        item = self._make_book_item()
        res = archiver.upsert_enriched_book(self.conn, item, do_archive_link=False)

        self.assertEqual(res["action"], "inserted")
        book_id = res["book_id"]
        self.assertGreater(book_id, 0)

        # 检查 books 表
        book = self.conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        self.assertIsNotNone(book)
        self.assertEqual(book["title"], "思考，快与慢")
        self.assertEqual(book["author"], "丹尼尔·卡尼曼")
        self.assertEqual(book["primary_format"], "epub")

        # 检查 book_files 表
        files = self.conn.execute(
            "SELECT * FROM book_files WHERE book_id = ?", (book_id,)
        ).fetchall()
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["is_primary"], 1)
        self.assertEqual(files[0]["format"], "epub")

    def test_case2_duplicate_file_reimport(self):
        """场景 2：完全相同的文件路径或哈希重复导入，保持幂等性并返回 duplicate_hash"""
        item1 = self._make_book_item(file_hash="hash_identical_123")
        res1 = archiver.upsert_enriched_book(self.conn, item1, do_archive_link=False)
        self.assertEqual(res1["action"], "inserted")

        # 相同 hash 再次导入
        item2 = self._make_book_item(
            file_path="/different/path/thinking.epub",
            file_hash="hash_identical_123",
        )
        res2 = archiver.upsert_enriched_book(self.conn, item2, do_archive_link=False)

        self.assertEqual(res2["action"], "duplicate_hash")
        self.assertEqual(res2["book_id"], res1["book_id"])

        # books 表总数不增加
        count = self.conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        self.assertEqual(count, 1)

    def test_case3_same_book_different_format(self):
        """场景 3：同书另一格式导入，合并格式并按格式优先级更新主文件"""
        # 先入库 mobi（低优先级，40 分）
        item_mobi = self._make_book_item(
            fmt="mobi",
            file_path="/tmp/mock/thinking.mobi",
            file_hash="hash_mobi_999",
            quality_score=60.0,
        )
        res_mobi = archiver.upsert_enriched_book(self.conn, item_mobi, do_archive_link=False)
        self.assertEqual(res_mobi["action"], "inserted")
        book_id = res_mobi["book_id"]

        # 检查初始 primary 为 mobi
        b_init = self.conn.execute("SELECT primary_format FROM books WHERE id = ?", (book_id,)).fetchone()
        self.assertEqual(b_init["primary_format"], "mobi")

        # 再导入同书的 epub（高优先级，100 分）
        item_epub = self._make_book_item(
            fmt="epub",
            file_path="/tmp/mock/thinking.epub",
            file_hash="hash_epub_888",
            quality_score=90.0,
        )
        res_epub = archiver.upsert_enriched_book(self.conn, item_epub, do_archive_link=False)
        self.assertEqual(res_epub["action"], "merged_format")
        self.assertEqual(res_epub["book_id"], book_id)

        # 确认升级为 epub 主格式
        b_updated = self.conn.execute(
            "SELECT primary_format, primary_path, quality_score FROM books WHERE id = ?",
            (book_id,),
        ).fetchone()
        self.assertEqual(b_updated["primary_format"], "epub")
        self.assertEqual(b_updated["primary_path"], "/tmp/mock/thinking.epub")
        self.assertEqual(b_updated["quality_score"], 90.0)

        # 确认 book_files 包含 2 个文件记录，且只有 epub 的 is_primary 为 1
        files = self.conn.execute(
            "SELECT format, is_primary FROM book_files WHERE book_id = ? ORDER BY id ASC",
            (book_id,),
        ).fetchall()
        self.assertEqual(len(files), 2)
        mobi_file = [f for f in files if f["format"] == "mobi"][0]
        epub_file = [f for f in files if f["format"] == "epub"][0]
        self.assertEqual(mobi_file["is_primary"], 0)
        self.assertEqual(epub_file["is_primary"], 1)

    def test_case4_same_title_different_author(self):
        """场景 4：同名不同书（书名长度>=3，且作者已知完全不同），分立为两本书，并记录冲突"""
        # 第一本《黑天鹅效应》 塔勒布
        item1 = self._make_book_item(
            title="黑天鹅效应",
            author="纳西姆·塔勒布",
            norm_title="黑天鹅效应",
            norm_author="纳西姆塔勒布",
            file_path="/tmp/mock/black_swan_taleb.epub",
            file_hash="hash_p1",
        )
        res1 = archiver.upsert_enriched_book(self.conn, item1, do_archive_link=False)
        self.assertEqual(res1["action"], "inserted")

        # 第二本同名《黑天鹅效应》 但作者已知完全不同（非未知作者，也非子串关系）
        item2 = self._make_book_item(
            title="黑天鹅效应",
            author="张三",
            norm_title="黑天鹅效应",
            norm_author="张三",
            file_path="/tmp/mock/black_swan_zhang.epub",
            file_hash="hash_p2",
        )
        res2 = archiver.upsert_enriched_book(self.conn, item2, do_archive_link=False)
        self.assertEqual(res2["action"], "inserted")
        self.assertNotEqual(res1["book_id"], res2["book_id"])

        # 验证数据库中存在 2 本独立书籍
        count = self.conn.execute("SELECT COUNT(*) FROM books WHERE title = '黑天鹅效应'").fetchone()[0]
        self.assertEqual(count, 2)

        # 验证产生了同名异书冲突记录进入 conflict_inbox
        conflict = self.conn.execute(
            "SELECT * FROM conflict_inbox WHERE conflict_type = 'ambiguous_duplicate'"
        ).fetchone()
        self.assertIsNotNone(conflict)
        self.assertEqual(conflict["detected_title"], "黑天鹅效应")
        self.assertEqual(conflict["candidate_book_id"], res1["book_id"])

    def test_case5_missing_author_routes_to_conflict_inbox(self):
        """场景 5：缺作者且无 ISBN 书籍正常入库，但登记进 conflict_inbox 待核对"""
        item = self._make_book_item(
            title="失落的未知古籍",
            author="未知作者",
            norm_title="失落的未知古籍",
            norm_author="未知作者",
            file_path="/tmp/mock/unknown_book.epub",
            file_hash="hash_unknown_author",
        )
        item["isbn"] = ""  # 既缺作者，又无 ISBN，触发 missing_author 告警
        res = archiver.upsert_enriched_book(self.conn, item, do_archive_link=False)
        self.assertEqual(res["action"], "inserted")

        # 验证进了 conflict_inbox
        conflict = self.conn.execute(
            "SELECT * FROM conflict_inbox WHERE conflict_type = 'missing_author'"
        ).fetchone()
        self.assertIsNotNone(conflict)
        self.assertEqual(conflict["detected_title"], "失落的未知古籍")
        self.assertEqual(conflict["detected_author"], "未知作者")
        self.assertEqual(conflict["status"], "pending")


if __name__ == "__main__":
    unittest.main()
