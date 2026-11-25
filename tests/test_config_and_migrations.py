import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from config import BASE_DIR, _resolve_dir, load_runtime_config
from database import get_conn, init_db


class ConfigAndMigrationsTest(unittest.TestCase):
    def test_load_runtime_config_handles_missing_and_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(load_runtime_config(root / "absent.json"), {})

            bad_file = root / "bad.json"
            bad_file.write_text("{not valid json", encoding="utf-8")
            self.assertEqual(load_runtime_config(bad_file), {})

            ok_file = root / "ok.json"
            ok_file.write_text('{"source_dirs": [{"name": "Test", "path": "/tmp"}]}', encoding="utf-8")
            cfg = load_runtime_config(ok_file)
            self.assertEqual(cfg["source_dirs"][0]["name"], "Test")

    def test_resolve_dir_handles_relative_absolute_and_empty(self):
        fallback = BASE_DIR / "Library"
        self.assertEqual(_resolve_dir("", fallback), fallback)
        self.assertEqual(_resolve_dir("custom_lib", fallback), (BASE_DIR / "custom_lib").resolve())
        abs_path = Path("/tmp/my_books")
        self.assertEqual(_resolve_dir(str(abs_path), fallback), abs_path)

    def test_marked_at_starts_from_when_a_marked_book_was_last_opened(self):
        """Schema version 8, run on a library that predates it: a marked book takes its last opening as the time
        of the mark, for want of anything better; an unmarked one, or one never opened, gets none, and a time
        already recorded stays."""
        from migrations import apply_migrations

        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "test.db"
            init_db(db_file)
            conn = get_conn(db_file)
            try:
                opened = "2026-10-01 08:00:00"
                marked_later = "2026-11-01 09:30:00"
                books = (  # id, is_favorite, last_opened_at, marked_at
                    (1, 1, opened, None), (2, 0, opened, None), (3, 1, None, None), (4, 1, "", None),
                    (5, 1, opened, marked_later))
                for book_id, is_favorite, last_opened_at, marked_at in books:
                    conn.execute(
                        "INSERT INTO books (id, dedup_key, title, norm_title, primary_format, primary_path, "
                        "is_favorite, last_opened_at, marked_at) VALUES (?, ?, 't', 't', 'epub', '/p', ?, ?, ?)",
                        (book_id, f"k{book_id}", is_favorite, last_opened_at, marked_at))
                expected = {1: opened, 2: None, 3: None, 4: None, 5: marked_later}
                for _ in range(2):  # a second run changes nothing
                    conn.execute("PRAGMA user_version = 7")
                    apply_migrations(conn)
                    conn.commit()
                    self.assertEqual(dict(conn.execute("SELECT id, marked_at FROM books").fetchall()), expected)
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 12)
            finally:
                conn.close()

    def test_init_db_applies_all_migrations_to_v9(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "test.db"
            init_db(db_file)
            conn = get_conn(db_file)
            try:
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                self.assertEqual(version, 12)
                indexes = {r["name"] for r in conn.execute("PRAGMA index_list(books)").fetchall()}
                self.assertIn("idx_books_category_classic", indexes)
                bf_cols = {r["name"] for r in conn.execute("PRAGMA table_info(book_files)").fetchall()}
                self.assertTrue({"embedded_title", "embedded_author", "isbn"}.issubset(bf_cols))
                b_cols = {r["name"] for r in conn.execute("PRAGMA table_info(books)").fetchall()}
                self.assertIn("is_deleted", b_cols)
                self.assertIn("marked_at", b_cols)
                tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                self.assertTrue({"ai_usage", "ai_rerank_cache", "title_repair"}.issubset(tables))
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
