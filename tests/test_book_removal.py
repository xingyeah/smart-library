"""Tests for book removal, physical file relocation to removed_books, and system-wide exclusion."""

import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import DATA_DIR, LIBRARY_DIR, REMOVED_BOOKS_DIR
from database import get_conn, init_db
from book_removal_service import (
    BookAlreadyDeletedError,
    BookNotFoundError,
    remove_book_and_relocate_files,
)
from archiver import upsert_enriched_book


class BookRemovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="lib_removal_test_"))
        self.test_src_file = self.tmp_dir / "test_book.epub"
        self.test_src_file.write_bytes(b"PK\x03\x04test_epub_binary_content")
        
        self.conn = get_conn()
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO books (
                dedup_key, title, norm_title, author, norm_author,
                primary_format, primary_path, archived_path, file_size, file_hash,
                is_deleted
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                "test_removal_key_999",
                "黑客与画家测试版",
                "黑客与画家测试版",
                "保罗·格雷厄姆",
                "保罗·格雷厄姆",
                "epub",
                str(self.test_src_file),
                "",
                len(self.test_src_file.read_bytes()),
                "hash_test_removal_999",
            ),
        )
        self.book_id = int(cur.lastrowid)
        cur.execute(
            """
            INSERT INTO book_files (
                book_id, original_path, archived_path, format, file_size, file_hash, is_primary
            ) VALUES (?, ?, '', 'epub', ?, 'hash_test_removal_999', 1)
            """,
            (self.book_id, str(self.test_src_file), len(self.test_src_file.read_bytes())),
        )
        self.conn.commit()

    def tearDown(self):
        cur = self.conn.cursor()
        cur.execute("DELETE FROM book_files WHERE book_id = ?", (self.book_id,))
        cur.execute("DELETE FROM books WHERE id = ?", (self.book_id,))
        self.conn.commit()
        self.conn.close()

        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir, ignore_errors=True)
        # Cleanup removed_books test folder if created
        target_dir = REMOVED_BOOKS_DIR / f"{self.book_id}_黑客与画家测试版"
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)

    def test_successful_removal_and_relocation(self):
        self.assertTrue(self.test_src_file.exists())
        res = remove_book_and_relocate_files(self.book_id)
        self.assertTrue(res["ok"])
        self.assertEqual(res["book_id"], self.book_id)

        # 1. Source file moved out of original place
        self.assertFalse(self.test_src_file.exists())

        # 2. Destination folder contains relocated file
        dest_dir = Path(res["dest_dir"])
        self.assertTrue(dest_dir.exists())
        relocated_file = dest_dir / "test_book.epub"
        self.assertTrue(relocated_file.exists())
        self.assertEqual(relocated_file.read_bytes(), b"PK\x03\x04test_epub_binary_content")

        # 3. Database record marked as is_deleted = 1
        row = self.conn.execute("SELECT is_deleted, archived_path, primary_path FROM books WHERE id = ?", (self.book_id,)).fetchone()
        self.assertEqual(int(row["is_deleted"]), 1)
        self.assertEqual(row["archived_path"], "")
        self.assertEqual(row["primary_path"], str(relocated_file))

    def test_repeat_removal_raises_already_deleted(self):
        remove_book_and_relocate_files(self.book_id)
        with self.assertRaises(BookAlreadyDeletedError):
            remove_book_and_relocate_files(self.book_id)

    def test_nonexistent_book_raises_not_found(self):
        with self.assertRaises(BookNotFoundError):
            remove_book_and_relocate_files(99999999)

    def test_archiver_scanner_skips_deleted_book(self):
        remove_book_and_relocate_files(self.book_id)
        # Simulate re-scanning a file matching the deleted book's hash or dedup_key
        item = {
            "dedup_key": "test_removal_key_999",
            "title": "黑客与画家测试版",
            "subtitle": "",
            "norm_title": "黑客与画家测试版",
            "author": "保罗·格雷厄姆",
            "norm_author": "保罗·格雷厄姆",
            "translator": "",
            "publisher": "",
            "pub_year": "2010",
            "isbn": "",
            "douban_id": "",
            "series": "",
            "volume_part": "",
            "edition_tag": "",
            "category": "计算机与AI",
            "tags": [],
            "language": "zh-CN",
            "page_count": 200,
            "format": "epub",
            "file_path": str(self.tmp_dir / "re_scanned.epub"),
            "file_size": 1024,
            "file_hash": "hash_test_removal_999",
            "source_name": "微信读书",
            "cover_path": "",
            "summary": "test",
            "toc_text": "",
            "quality_score": 80.0,
        }
        res = upsert_enriched_book(self.conn, item, do_archive_link=False)
        self.assertEqual(res.get("action"), "skipped_deleted")
        self.assertEqual(res.get("book_id"), self.book_id)

    def test_http_delete_handler_and_detail_exclusion(self):
        from unittest.mock import MagicMock
        from http_api.handlers.books import handle_get_book_detail, handle_post_book_delete

        mock_handler = MagicMock()
        mock_handler.wfile = MagicMock()

        # Call delete endpoint
        handle_post_book_delete(mock_handler, {}, {}, str(self.book_id))
        mock_handler.send_response.assert_called_with(200)

        # Subsequent detail request must return 404
        mock_handler_get = MagicMock()
        handle_get_book_detail(mock_handler_get, {}, {}, str(self.book_id))
        mock_handler_get.send_response.assert_called_with(404)


if __name__ == "__main__":
    unittest.main()
