"""The tonight page: skip records, the book to carry on with, and picks that leave out what was shown or skipped."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import database  # noqa: E402
import skips  # noqa: E402
import tonight  # noqa: E402
from http_api.handlers import tonight as tonight_handlers  # noqa: E402


class TonightTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "tonight.db"
        database.init_db(self.db_path)
        for target, factory in (
            ("skips.write_tx", lambda: database.write_tx(self.db_path)),
            ("skips.get_conn", lambda: database.get_conn(self.db_path)),
            ("http_api.handlers.tonight.get_conn", lambda: database.get_conn(self.db_path)),
        ):
            patcher = mock.patch(target, factory)
            patcher.start()
            self.addCleanup(patcher.stop)

    def add_book(self, book_id, status="unread", opened=None, progress=None, deleted=0):
        conn = database.get_conn(self.db_path)
        conn.execute(
            "INSERT INTO books (id, dedup_key, title, norm_title, author, norm_author, primary_format, primary_path, "
            "reading_status, last_opened_at, read_progress, is_deleted) VALUES (?, ?, ?, ?, 'a', 'a', 'epub', ?, ?, ?, ?, ?)",
            (book_id, f"k{book_id}", f"书{book_id}", f"书{book_id}", f"/tmp/{book_id}", status, opened, progress, deleted),
        )
        conn.commit()
        conn.close()

    def skip_count(self):
        conn = database.get_conn(self.db_path)
        try:
            return conn.execute("SELECT COUNT(*) FROM book_skips").fetchone()[0]
        finally:
            conn.close()


class SkipRecordTests(TonightTestCase):
    def test_tonight_skip_hides_the_book_and_search_skip_only_records(self):
        self.add_book(1)
        self.add_book(2)
        skips.record_skip(1, skips.SKIP_CONTEXT_TONIGHT, "ignored")
        skips.record_skip(2, skips.SKIP_CONTEXT_SEARCH, "  量子力学  ")
        self.assertEqual(skips.recently_skipped_ids(), {1})
        conn = database.get_conn(self.db_path)
        stored = {row["book_id"]: row["query"] for row in conn.execute("SELECT book_id, query FROM book_skips")}
        conn.close()
        self.assertEqual(stored, {1: "", 2: "量子力学"})

    def test_undo_removes_only_the_latest_skip(self):
        self.add_book(1)
        skips.record_skip(1, skips.SKIP_CONTEXT_TONIGHT)
        skips.record_skip(1, skips.SKIP_CONTEXT_SEARCH, "q")
        self.assertTrue(skips.undo_skip(1))
        self.assertEqual(self.skip_count(), 1)
        self.assertTrue(skips.undo_skip(1))
        self.assertFalse(skips.undo_skip(1))

    def test_old_skips_stop_hiding_but_stay_recorded(self):
        self.add_book(1)
        skips.record_skip(1, skips.SKIP_CONTEXT_TONIGHT)
        conn = database.get_conn(self.db_path)
        conn.execute("UPDATE book_skips SET created_at = datetime('now', '-31 days')")
        conn.commit()
        conn.close()
        self.assertEqual(skips.recently_skipped_ids(), set())
        self.assertEqual(self.skip_count(), 1)

    def test_unknown_context_and_missing_book_are_refused(self):
        self.add_book(1)
        self.add_book(2, deleted=1)
        for book_id, context in ((1, "elsewhere"), (99, "tonight"), (2, "tonight")):
            with self.assertRaises(skips.SkipError):
                skips.record_skip(book_id, context)
        self.assertEqual(self.skip_count(), 0)


class ContinueReadingTests(TonightTestCase):
    def test_latest_opened_unfinished_reading_book_comes_first(self):
        self.add_book(1, "reading", "2026-11-01 10:00:00", 0.2)
        self.add_book(2, "reading", "2026-11-03 10:00:00", 0.5)
        self.add_book(3, "reading", None, 0.1)  # marked as reading but never opened
        self.add_book(4, "reading", "2026-11-04 10:00:00", 1.0)  # finished
        self.add_book(5, "want", "2026-11-05 10:00:00", 0.3)
        conn = database.get_conn(self.db_path)
        try:
            self.assertEqual(tonight.continue_reading_row(conn)["id"], 2)
            self.assertEqual(tonight.other_reading_count(conn, True), 3)
            self.assertEqual(tonight.other_reading_count(conn, False), 4)
        finally:
            conn.close()

    def test_no_candidate_gives_none(self):
        self.add_book(1, "unread")
        conn = database.get_conn(self.db_path)
        try:
            self.assertIsNone(tonight.continue_reading_row(conn))
        finally:
            conn.close()


class PicksTests(TonightTestCase):
    def test_picks_leave_out_seen_and_recently_skipped_books(self):
        self.add_book(1)
        skips.record_skip(1, skips.SKIP_CONTEXT_TONIGHT)
        fake = {"items": [{"id": 7, "reason_text": "与你标记的《甲》相近"}, {"id": 8}]}
        with mock.patch.object(tonight, "recommend_for_tonight", return_value=fake) as recommend:
            picks = tonight.tonight_picks([5, 6])
        self.assertEqual(recommend.call_args[0], (tonight.TONIGHT_PICKS, {1, 5, 6}))
        self.assertEqual([p["tonight_reason"] for p in picks], ["与你标记的《甲》相近", ""])


class HandlerTests(TonightTestCase):
    def call(self, handler_fn, *args):
        sent = []
        with mock.patch.object(tonight_handlers, "send_json", lambda _h, data, status=200: sent.append((status, data))):
            handler_fn(None, *args)
        return sent[0]

    def test_tonight_excludes_the_continue_book_and_shown_picks(self):
        self.add_book(3, "reading", "2026-11-03 10:00:00", 0.4)
        with mock.patch.object(tonight_handlers, "tonight_picks", return_value=[]) as picks:
            status, data = self.call(tonight_handlers.handle_get_tonight, {"seen": ["7,x,8"]}, {})
        self.assertEqual((status, data["continue"]["id"], data["other_reading"]), (200, 3, 0))
        self.assertEqual(picks.call_args[0][0], [7, 8, 3])

    def test_skip_route_rejects_bad_context_and_accepts_good(self):
        self.add_book(1)
        status, _ = self.call(tonight_handlers.handle_post_skip, {}, {"book_id": 1, "context": "nope"})
        self.assertEqual(status, 400)
        status, _ = self.call(tonight_handlers.handle_post_skip, {}, {"book_id": "1", "context": "tonight"})
        self.assertEqual(status, 400)
        status, data = self.call(tonight_handlers.handle_post_skip, {}, {"book_id": 1, "context": "tonight"})
        self.assertEqual((status, data), (200, {"ok": True}))
        status, data = self.call(tonight_handlers.handle_post_skip_undo, {}, {"book_id": 1})
        self.assertEqual(data, {"ok": True})
        status, _ = self.call(tonight_handlers.handle_post_skip_undo, {}, {})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
