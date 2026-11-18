"""Splitting a mismerged record and undoing it, on a temp DB and temp Library."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import archiver  # noqa: E402
import book_removal_service  # noqa: E402
import database  # noqa: E402
import mismerge_service as ms  # noqa: E402
import split_undo  # noqa: E402


class SplitAndUndoTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "t.db"
        database.init_db(self.db)
        lib = self.root / "Library"
        connect = lambda: database.get_conn(self.db)  # noqa: E731
        patches = [mock.patch.object(m, "get_conn", connect) for m in (ms, split_undo)]
        patches += [mock.patch.object(archiver, "LIBRARY_DIR", lib), mock.patch.object(ms, "LIBRARY_DIR", lib),
                    mock.patch.object(split_undo, "LIBRARY_DIR", lib), mock.patch.object(book_removal_service, "LIBRARY_DIR", lib),
                    mock.patch.object(ms, "DECISIONS_PATH", self.root / "none.json"),
                    mock.patch.object(ms, "_after_split"), mock.patch.object(ms, "backup_database", lambda: "b"),
                    mock.patch("split_undo.spawn_reindex")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.conn = connect()
        self.addCleanup(self.conn.close)

    def _record(self):
        """'Depression' with two Wittgenstein copies merged in; the stray pdf is the primary."""
        names = ["Wittgenstein A Very Short Introduction (A. C. Grayling).txt",
                 "Depression A Very Short Introduction (Jan Scott).txt",
                 "Wittgenstein A Very Short Introduction (Grayling, Anthony C).txt"]
        bid = self.conn.execute(
            "INSERT INTO books (dedup_key, title, norm_title, author, primary_format, primary_path, archived_path, "
            "is_favorite) VALUES ('k', 'Depression A Very Short Introduction', 'depression', 'Jan Scott', 'txt', ?, '', 1)",
            (str(self.root / names[0]),)).lastrowid
        for i, name in enumerate(names):
            path = self.root / name
            path.write_text(f"{name}\n" * 50, encoding="utf-8")
            self.conn.execute("INSERT INTO book_files (book_id, original_path, archived_path, format, file_size, "
                              "file_hash, is_primary) VALUES (?, ?, '', 'txt', ?, ?, ?)",
                              (bid, str(path), path.stat().st_size, f"h{i}", int(i == 0)))
        self.conn.commit()
        return bid

    def test_split_keeps_user_data_and_undo_restores_everything(self):
        bid = self._record()
        before = [tuple(r) for r in self.conn.execute("SELECT id, book_id, is_primary, archived_path FROM book_files")]
        res = ms.split_mismerged_book(bid)
        self.assertTrue(res["ok"], res)
        self.assertEqual(len(res["created_books"]), 1)
        new_id = res["created_books"][0]["new_book_id"]
        book = self.conn.execute("SELECT * FROM books WHERE id = ?", (bid,)).fetchone()
        self.assertEqual(book["is_favorite"], 1)
        self.assertIn("Depression", book["primary_path"])
        self.assertTrue(Path(book["archived_path"]).exists())  # the anchor got a Library link
        moved = self.conn.execute("SELECT COUNT(*) FROM book_files WHERE book_id = ?", (new_id,)).fetchone()[0]
        self.assertEqual(moved, 2)
        self.assertIn("Wittgenstein", self.conn.execute("SELECT title FROM books WHERE id = ?", (new_id,)).fetchone()[0])

        undo = split_undo.undo_split_run(res["run_id"])
        self.assertEqual(undo["removed_books"], [new_id])
        after = [tuple(r) for r in self.conn.execute("SELECT id, book_id, is_primary, archived_path FROM book_files")]
        self.assertEqual(after, before)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM books WHERE id = ?", (new_id,)).fetchone())
        restored = self.conn.execute("SELECT primary_path, archived_path FROM books WHERE id = ?", (bid,)).fetchone()
        self.assertEqual(tuple(restored), (str(self.root / "Wittgenstein A Very Short Introduction (A. C. Grayling).txt"), ""))
        with self.assertRaises(ValueError):
            split_undo.undo_split_run(res["run_id"])

    def test_missing_file_leaves_the_record_untouched(self):
        bid = self._record()
        stray = self.conn.execute("SELECT original_path FROM book_files WHERE is_primary = 1").fetchone()[0]
        Path(stray).unlink()
        res = ms.split_mismerged_book(bid)
        self.assertFalse(res["ok"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM books").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(DISTINCT book_id) FROM book_files").fetchone()[0], 1)

    def test_user_decisions_are_followed(self):
        bid = self._record()
        keep = self.conn.execute("SELECT id FROM book_files WHERE original_path LIKE '%Anthony%'").fetchone()[0]
        (self.root / "none.json").write_text(json.dumps({str(keep): "same"}), encoding="utf-8")
        res = ms.split_mismerged_book(bid)
        self.assertEqual(res["created_books"][0]["file_count"], 1)
        self.assertEqual(self.conn.execute("SELECT book_id FROM book_files WHERE id = ?", (keep,)).fetchone()[0], bid)

    def test_work_already_in_the_library_is_joined_not_duplicated(self):
        bid = self._record()
        stray = self.conn.execute("SELECT * FROM book_files WHERE is_primary = 1").fetchone()
        item, _ = ms._new_book_item(stray, stray["original_path"])
        other = self.conn.execute(
            "INSERT INTO books (dedup_key, title, norm_title, primary_format) VALUES (?, 'Wittgenstein', 'w', 'txt')",
            (item["dedup_key"],)).lastrowid
        copy = self.root / "Wittgenstein A Very Short Introduction (Grayling).txt"
        copy.write_text("x", encoding="utf-8")
        self.conn.execute("INSERT INTO book_files (book_id, original_path, format, file_hash, is_primary) "
                          "VALUES (?, ?, 'txt', 'hx', 1)", (other, str(copy)))
        self.conn.commit()
        res = ms.split_mismerged_book(bid)
        self.assertEqual(res["created_books"][0]["joined_book_id"], other)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM book_files WHERE book_id = ?", (other,)).fetchone()[0], 3)
        split_undo.undo_split_run(res["run_id"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM book_files WHERE book_id = ?", (other,)).fetchone()[0], 1)
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM books WHERE id = ?", (other,)).fetchone())


if __name__ == "__main__":
    unittest.main()
