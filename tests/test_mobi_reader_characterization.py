import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Add backend directory to path
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import reader
from reader import sanitize_and_format_book_html


class TestMobiReaderCharacterization(unittest.TestCase):
    """
    阶段零补充特征测试：锁定 reader._build_mobi_azw3_structure 的章节抽取、
    目录解析与 HTML 清洗特征。
    """

    SAMPLE_AZW3 = Path(__file__).resolve().parents[1] / "Library" / "朱迪亚·珀尔 _ 达纳·麦肯齐" / "为什么" / "为什么.azw3"

    def test_invalid_header_raises_value_error(self):
        """测试异常样本：文件小于 78 字节或头损毁时抛出明确的 ValueError"""
        with tempfile.NamedTemporaryFile(suffix=".azw3", delete=False) as f:
            f.write(b"SHORT_HEADER")
            tmp_path = f.name

        try:
            with self.assertRaises(ValueError) as ctx:
                reader._build_mobi_azw3_structure(
                    book_id=99999,
                    fpath=tmp_path,
                    cache_get=lambda b, m: None,
                    cache_put=lambda b, m, v: None,
                )
            self.assertIn("Invalid MOBI/AZW3 header", str(ctx.exception))
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_cache_hit_bypasses_parsing(self):
        """测试缓存命中逻辑：当 cache_get 返回非 None 时直接复用，不重复解析磁盘"""
        if not self.SAMPLE_AZW3.exists():
            self.skipTest(f"AZW3 样本文件不存在: {self.SAMPLE_AZW3}")
        fake_cache = {"mode": "split", "chapters": [{"index": 0, "title": "缓存章节"}], "toc": []}
        res = reader._build_mobi_azw3_structure(
            book_id=123,
            fpath=str(self.SAMPLE_AZW3),
            cache_get=lambda b, m: fake_cache,
            cache_put=lambda b, m, v: None,
        )
        self.assertEqual(res, fake_cache)

    def test_real_azw3_structure_and_toc(self):
        """用真实 AZW3 样本固定核心特征：章节总数、TOC 目录数与层级标题"""
        if not self.SAMPLE_AZW3.exists():
            self.skipTest(f"AZW3 样本文件不存在: {self.SAMPLE_AZW3}")

        cache_store = {}

        def _get(b, m):
            return cache_store.get((b, m))

        def _put(b, m, v):
            cache_store[(b, m)] = v

        struct_data = reader._build_mobi_azw3_structure(
            book_id=1236,
            fpath=str(self.SAMPLE_AZW3),
            cache_get=_get,
            cache_put=_put,
        )

        # 1. 验证基础顶级键
        self.assertEqual(struct_data.get("mode"), "mobi")
        self.assertIn("chapters", struct_data)
        self.assertIn("toc", struct_data)
        self.assertIn("first_image_idx", struct_data)

        # 2. 锁定章节数与目录数
        chapters = struct_data["chapters"]
        toc = struct_data["toc"]
        self.assertEqual(len(chapters), 19, "固定 AZW3 样本章节数应为 19")
        self.assertEqual(len(toc), 104, "固定 AZW3 样本目录项数应为 104")

        # 3. 锁定前 5 个目录与章节标题
        expected_top5 = [
            "扉页",
            "目录",
            "谨以此书献给露丝",
            "推荐序 了解因果关系科学，从珀尔的《为什么》开始",
            "前言",
        ]
        actual_toc_top5 = [t["title"] for t in toc[:5]]
        actual_chap_top5 = [c["title"] for c in chapters[:5]]
        self.assertEqual(actual_toc_top5, expected_top5)
        self.assertEqual(actual_chap_top5, expected_top5)

        # 4. 章节字段格式校验
        first_chap = chapters[0]
        self.assertEqual(first_chap["index"], 0)
        self.assertEqual(first_chap["title"], "扉页")
        self.assertIn("html", first_chap)
        self.assertGreater(len(first_chap["html"]), 0)

        # 5. 验证 extract_book_reader_content 对 AZW3 的完整集成
        from database import get_conn
        conn = get_conn()
        real_row = conn.execute("SELECT * FROM books WHERE id = 1236").fetchone()
        if real_row:
            book_row = real_row
        else:
            book_row = {
                "id": 1236,
                "title": "为什么",
                "author": "朱迪亚·珀尔",
                "language": "zh-cn",
                "summary": "关于因果关系的新科学",
                "category": "人工智能与技术",
                "primary_format": "azw3",
                "primary_path": str(self.SAMPLE_AZW3),
                "archived_path": str(self.SAMPLE_AZW3),
            }
        content = reader.extract_book_reader_content(book_row, chapter_idx=0)
        self.assertEqual(content["total_chapters"], 19)
        self.assertEqual(content["current_chapter"], 0)
        self.assertEqual(content["chapter_title"], "扉页")
        self.assertIn("<div", content["html"])
        # 清洗后的 HTML 不应包含原始恶意的 script/onclick
        self.assertNotIn("<script", content["html"].lower())


if __name__ == "__main__":
    unittest.main()
