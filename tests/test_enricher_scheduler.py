import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import database


class TestEnricherScheduler(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "test_scheduler.db"
        database.init_db(self.db_path)

        conn = database.get_conn(self.db_path)
        cur = conn.execute(
            """
            INSERT INTO books (
                dedup_key, title, norm_title, author, norm_author,
                isbn, language, classic_score
            ) VALUES (
                'test::en::1', 'Atomic Habits', 'atomic habits', 'James Clear', 'james clear',
                '9780735211292', 'en', 60.0
            )
            """
        )
        self.en_book_id = cur.lastrowid
        cur2 = conn.execute(
            """
            INSERT INTO books (
                dedup_key, title, norm_title, author, norm_author,
                isbn, language, classic_score
            ) VALUES (
                'test::zh::1', '原子习惯', '原子习惯', '詹姆斯·克利尔', '詹姆斯克利尔',
                '9787514378772', 'zh-CN', 60.0
            )
            """
        )
        self.zh_book_id = cur2.lastrowid
        conn.commit()
        conn.close()

    def test_circuit_breaker_isolation(self):
        from enrichers.scheduler import CircuitBreakerManager

        cb = CircuitBreakerManager()
        self.assertTrue(cb.is_available("douban"))
        self.assertTrue(cb.is_available("google_books"))

        # 豆瓣发生 429 触发退避
        cb.trigger_cooldown("douban", duration_sec=60.0)
        self.assertFalse(cb.is_available("douban"))
        # Google Books 通道不受影响
        self.assertTrue(cb.is_available("google_books"))

    def test_scheduler_routes_by_language(self):
        from enrichers.base import EnrichedBookMeta
        from enrichers.scheduler import MultiSourceSlowEnricher

        enricher = MultiSourceSlowEnricher(db_path=self.db_path)

        mock_gb_res = EnrichedBookMeta(
            source="google_books",
            external_id="vol_123",
            rating=9.2,
            raw_rating=4.6,
            votes=5000,
            description="Tiny changes, remarkable results.",
            reviews=[],
            status="success",
        )
        mock_db_res = EnrichedBookMeta(
            source="douban",
            external_id="subj_456",
            rating=8.9,
            raw_rating=8.9,
            votes=12000,
            description="从改变一个微小的习惯开始。",
            reviews=[{"author": "读者", "content": "好书", "source": "douban"}],
            status="success",
        )

        with mock.patch.object(enricher.google_books, "fetch", return_value=mock_gb_res) as p_gb, \
             mock.patch.object(enricher.douban, "fetch", return_value=mock_db_res) as p_db:

            conn = database.get_conn(self.db_path)
            en_row = conn.execute("SELECT * FROM books WHERE id = ?", (self.en_book_id,)).fetchone()
            zh_row = conn.execute("SELECT * FROM books WHERE id = ?", (self.zh_book_id,)).fetchone()

            # 英文书聚合各渠道
            res_en = enricher.enrich_book_row(en_row)
            self.assertTrue(res_en)
            p_gb.assert_called_once()
            p_db.assert_called_once()

            # 验证从表与主表 classic_score 更新
            ext_meta_en = database.get_external_meta_by_book(self.db_path, self.en_book_id)
            self.assertEqual(len(ext_meta_en), 2)
            sources = {it["source"] for it in ext_meta_en}
            self.assertIn("google_books", sources)
            self.assertIn("douban", sources)

            updated_en = conn.execute("SELECT classic_score FROM books WHERE id = ?", (self.en_book_id,)).fetchone()
            self.assertGreater(updated_en["classic_score"], 60.0)
            conn.close()


    def candidate_ids(self):
        """Every book the enricher would pick, in turn (each pick is hidden inside a rolled-back transaction)."""
        from enrichers.scheduler import _fetch_next_enrich_candidate

        conn = database.get_conn(self.db_path)
        try:
            found = set()
            row = _fetch_next_enrich_candidate(conn)
            while row is not None:
                found.add(row["id"])
                conn.execute("UPDATE books SET is_anthology = 1 WHERE id = ?", (row["id"],))
                row = _fetch_next_enrich_candidate(conn)
            conn.rollback()
            return found
        finally:
            conn.close()

    def test_unanswered_channels_do_not_mark_the_book_missing(self):
        from enrichers.scheduler import RETRY_STATUS, MultiSourceSlowEnricher

        enricher = MultiSourceSlowEnricher(db_path=self.db_path)
        conn = database.get_conn(self.db_path)
        row = conn.execute("SELECT * FROM books WHERE id = ?", (self.zh_book_id,)).fetchone()
        conn.close()
        enricher.cb.trigger_cooldown("google_books", duration_sec=60.0)  # one channel cooling down
        with mock.patch.object(enricher.douban, "fetch", side_effect=OSError("network down")), \
             mock.patch.object(enricher.open_library, "fetch", return_value=None):
            self.assertFalse(enricher.enrich_book_row(row))

        metas = database.get_external_meta_by_book(self.db_path, self.zh_book_id)
        self.assertEqual([(m["source"], m["status"]) for m in metas], [("none", RETRY_STATUS)])
        self.assertNotIn(self.zh_book_id, self.candidate_ids())

        conn = database.get_conn(self.db_path)
        conn.execute("UPDATE book_external_meta SET fetched_at = datetime('now', '-8 days')")
        conn.commit()
        conn.close()
        self.assertIn(self.zh_book_id, self.candidate_ids())

    def test_every_channel_missing_marks_not_found(self):
        from enrichers.scheduler import MultiSourceSlowEnricher

        enricher = MultiSourceSlowEnricher(db_path=self.db_path)
        conn = database.get_conn(self.db_path)
        row = conn.execute("SELECT * FROM books WHERE id = ?", (self.en_book_id,)).fetchone()
        conn.close()
        with mock.patch.object(enricher.douban, "fetch", return_value=None), \
             mock.patch.object(enricher.google_books, "fetch", return_value=None), \
             mock.patch.object(enricher.open_library, "fetch", return_value=None):
            self.assertFalse(enricher.enrich_book_row(row))
        metas = database.get_external_meta_by_book(self.db_path, self.en_book_id)
        self.assertEqual([(m["source"], m["status"]) for m in metas], [("none", "not_found")])
        self.assertNotIn(self.en_book_id, self.candidate_ids())


class ExternalBonusTest(unittest.TestCase):
    def test_few_votes_give_no_bonus(self):
        from enrichers.scheduler import compute_external_bonus

        self.assertEqual(compute_external_bonus(10.0, 1), 0.0)  # Google Books: 5 stars from one reader
        self.assertEqual(compute_external_bonus(10.0, 9), 0.0)
        self.assertGreater(compute_external_bonus(9.0, 24), 0.0)


if __name__ == "__main__":
    unittest.main()
