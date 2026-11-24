"""The desktop home's batch bar and multi-pick filters: marks set on many books in one go, and facets that take
several values."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import book_marks  # noqa: E402
import database  # noqa: E402
from http_api.handlers import marks as marks_handlers  # noqa: E402
from search.filters import build_book_facet_where  # noqa: E402
from search.types import ShelfFilters  # noqa: E402


class BatchMarksTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "marks.db"
        database.init_db(self.db_path)
        patcher = mock.patch("book_marks.write_tx", lambda: database.write_tx(self.db_path))
        patcher.start()
        self.addCleanup(patcher.stop)

    def add_book(self, book_id, **cols):
        values = {"category": "心理学与脑科学", "primary_format": "epub", "reading_status": "unread",
                  "is_favorite": 0, "pub_year": "", "is_deleted": 0, **cols}
        conn = database.get_conn(self.db_path)
        conn.execute(
            "INSERT INTO books (id, dedup_key, title, norm_title, author, norm_author, primary_path, category, "
            "primary_format, reading_status, is_favorite, pub_year, is_deleted) "
            "VALUES (?, ?, ?, ?, 'a', 'a', ?, ?, ?, ?, ?, ?, ?)",
            (book_id, f"k{book_id}", f"书{book_id}", f"书{book_id}", f"/tmp/{book_id}", values["category"],
             values["primary_format"], values["reading_status"], values["is_favorite"], values["pub_year"],
             values["is_deleted"]),
        )
        conn.commit()
        conn.close()

    def marks_of(self, book_id):
        conn = database.get_conn(self.db_path)
        try:
            row = conn.execute("SELECT reading_status, is_favorite, marked_at FROM books WHERE id = ?",
                               (book_id,)).fetchone()
            return dict(row)
        finally:
            conn.close()


class ApplyBatchMarksTests(BatchMarksTestCase):
    def test_sets_every_book_and_hands_back_the_previous_marks(self):
        self.add_book(1, reading_status="read")
        self.add_book(2, is_favorite=1)
        previous = book_marks.apply_batch_marks(book_marks.parse_batch_items(
            [{"id": 1, "reading_status": "want"}, {"id": 2, "reading_status": "want"}]))
        self.assertEqual(previous, [{"id": 1, "reading_status": "read", "is_favorite": False},
                                    {"id": 2, "reading_status": "unread", "is_favorite": True}])
        self.assertEqual(self.marks_of(1)["reading_status"], "want")
        self.assertIsNotNone(self.marks_of(2)["marked_at"])

        book_marks.apply_batch_marks(book_marks.parse_batch_items(previous))
        self.assertEqual(self.marks_of(1)["reading_status"], "read")
        self.assertEqual((self.marks_of(2)["reading_status"], self.marks_of(2)["is_favorite"]), ("unread", 1))

    def test_a_missing_or_deleted_book_writes_nothing(self):
        self.add_book(1)
        self.add_book(2, is_deleted=1)
        parsed = book_marks.parse_batch_items([{"id": 1, "is_favorite": True}, {"id": 2, "is_favorite": True}])
        with self.assertRaisesRegex(book_marks.BatchMarksError, r"\[2\]"):
            book_marks.apply_batch_marks(parsed)
        self.assertEqual(self.marks_of(1)["is_favorite"], 0)

    def test_malformed_batches_are_refused(self):
        too_many = [{"id": i, "is_favorite": True} for i in range(1, book_marks.MAX_BATCH_BOOKS + 2)]
        for items in ([], None, too_many, [{"id": "1", "is_favorite": True}], [{"id": True, "is_favorite": True}],
                      [{"id": 1}], [{"id": 1, "reading_status": "done"}],
                      [{"id": 1, "is_favorite": True}, {"id": 1, "is_favorite": False}]):
            with self.assertRaises(book_marks.BatchMarksError, msg=repr(items)[:60]):
                book_marks.parse_batch_items(items)


class BatchMetaHandlerTests(BatchMarksTestCase):
    def call(self, body):
        sent = []
        with mock.patch.object(marks_handlers, "send_json", lambda _h, data, status=200: sent.append((status, data))), \
                mock.patch.object(marks_handlers, "enqueue_book_summary_refresh") as refresh:
            marks_handlers.handle_post_batch_meta(None, {}, body)
        return sent[0], refresh

    def test_answers_the_count_and_refreshes_summaries_of_wanted_books(self):
        self.add_book(1)
        self.add_book(2)
        (status, data), refresh = self.call({"items": [{"id": 1, "reading_status": "want"},
                                                       {"id": 2, "reading_status": "read"}]})
        self.assertEqual((status, data["updated"]), (200, 2))
        refresh.assert_called_once_with(1)

    def test_bad_batch_is_a_400(self):
        (status, data), _ = self.call({"items": [{"id": 9, "is_favorite": True}]})
        self.assertEqual(status, 400)
        self.assertIn("not found", data["error"])


class MultiValueFacetTests(BatchMarksTestCase):
    def matching_ids(self, filters):
        clauses, params = build_book_facet_where(filters)
        conn = database.get_conn(self.db_path)
        try:
            sql = f"SELECT id FROM books WHERE {' AND '.join(clauses)} ORDER BY id"
            return [row["id"] for row in conn.execute(sql, params)]
        finally:
            conn.close()

    def test_values_within_a_group_widen_and_groups_narrow(self):
        self.add_book(1, category="历史与地缘政治", primary_format="pdf", pub_year="2015")
        self.add_book(2, category="数学与统计", primary_format="epub", pub_year="1985")
        self.add_book(3, category="心理学与脑科学", primary_format="epub", pub_year="2021")
        self.assertEqual(self.matching_ids(ShelfFilters(category="历史与地缘政治,数学与统计")), [1, 2])
        self.assertEqual(self.matching_ids(ShelfFilters(category="历史与地缘政治,数学与统计", fmt="epub")), [2])
        self.assertEqual(self.matching_ids(ShelfFilters(pub_period="2010s,2020s")), [1, 3])
        self.assertEqual(self.matching_ids(ShelfFilters(category="全部")), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
