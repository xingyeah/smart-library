"""Deleting a book with several files: nothing moves until the caller confirms the exact file list. Temp DB only."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import book_removal_service as removal  # noqa: E402
import database  # noqa: E402


class DeleteConfirmationTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        db_path = self.root / "t.db"
        database.init_db(db_path)
        for patch in (
            mock.patch.object(removal, "get_conn", lambda: database.get_conn(db_path)),
            mock.patch.object(removal, "REMOVED_BOOKS_DIR", self.root / "removed"),
            mock.patch.object(removal, "spawn_reindex"),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.conn = database.get_conn(db_path)
        self.addCleanup(self.conn.close)

    def _book(self, names):
        paths = []
        for name in names:
            p = self.root / name
            p.write_bytes(name.encode())
            paths.append(p)
        cur = self.conn.execute(
            "INSERT INTO books (dedup_key, title, norm_title, primary_format, primary_path) VALUES (?, 't', 't', 'epub', ?)",
            (names[0], str(paths[0])),
        )
        bid = cur.lastrowid
        ids = [self.conn.execute(
            "INSERT INTO book_files (book_id, original_path, format, is_primary) VALUES (?, ?, ?, ?)",
            (bid, str(p), p.suffix[1:], int(i == 0))).lastrowid for i, p in enumerate(paths)]
        self.conn.commit()
        return bid, ids, paths

    def test_multi_file_delete_waits_for_the_exact_list(self):
        bid, ids, paths = self._book([
            "Hackers and Painters (Paul Graham).epub",
            "Hackers and Painters - Graham.pdf",
            "Unmasking the Face (Paul Ekman).pdf",  # a different book merged in by mistake
        ])
        with self.assertRaises(removal.DeleteNeedsConfirmation) as need:
            removal.remove_book_and_relocate_files(bid)
        flags = {f["name"]: f["looks_like_other_book"] for f in need.exception.files}
        self.assertEqual(flags, {paths[0].name: False, paths[1].name: False, paths[2].name: True})
        with self.assertRaises(removal.DeleteNeedsConfirmation):
            removal.remove_book_and_relocate_files(bid, ids[:2])  # a partial or stale list is refused
        self.assertTrue(all(p.exists() for p in paths))  # nothing moved yet
        res = removal.remove_book_and_relocate_files(bid, ids)
        self.assertEqual(len(res["moved_files"]), 3)
        self.assertFalse(any(p.exists() for p in paths))

    def test_single_file_needs_no_list(self):
        bid, _, paths = self._book(["Thinking, Fast and Slow (Daniel Kahneman).epub"])
        self.assertTrue(removal.remove_book_and_relocate_files(bid)["ok"])
        self.assertFalse(paths[0].exists())


if __name__ == "__main__":
    unittest.main()
