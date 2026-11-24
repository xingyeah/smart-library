import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import auto_summary_refresher as asr
import book_enrichment_service as bes
import database
import deepseek_client as ds


def _insert_book(conn, dedup_key: str, title: str, **kwargs) -> int:
    cols = {
        "dedup_key": dedup_key,
        "title": title,
        "norm_title": title.lower(),
        "author": "Tester",
        "norm_author": "tester",
        "summary": kwargs.get("summary", ""),
        "summary_source": kwargs.get("summary_source", ""),
        "target_audience": kwargs.get("target_audience", ""),
        "core_points": kwargs.get("core_points", "[]"),
        "is_favorite": kwargs.get("is_favorite", 0),
        "reading_status": kwargs.get("reading_status", "unread"),
        "douban_rating": kwargs.get("douban_rating", 0.0),
        "is_deleted": kwargs.get("is_deleted", 0),
    }
    keys = ", ".join(cols.keys())
    ph = ", ".join("?" for _ in cols)
    cur = conn.execute(f"INSERT INTO books ({keys}) VALUES ({ph})", list(cols.values()))
    conn.commit()
    return int(cur.lastrowid)


def _insert_ai_meta(conn, book_id: int, model: str = "deepseek-flash") -> None:
    conn.execute(
        """
        INSERT INTO book_ai_meta
            (book_id, clean_title, keywords, level, kind, ai_category, subfield, input_hash, model, version)
        VALUES (?, 't', '[]', '入门', '技术工程', '人工智能与技术', 'AI', 'h', ?, 1)
        """,
        (book_id, model),
    )
    conn.commit()


class SummaryValidationTest(unittest.TestCase):
    def test_valid_and_invalid_summaries(self):
        good = "本书系统阐述分布式系统的核心原理与工程实践，涵盖共识算法、复制状态机、分片与容错设计。"
        self.assertTrue(asr.is_summary_valid_chinese(good))
        self.assertFalse(asr.is_summary_valid_chinese(""))
        self.assertFalse(asr.is_summary_valid_chinese("太短了"))
        self.assertFalse(asr.is_summary_valid_chinese("An introduction to variational calculus and optimization methods."))
        self.assertFalse(asr.is_summary_valid_chinese("（CIP）数据 某书/张三著 -- ISBN 978-7-000-00000-0 本书介绍很多内容超过三十五个字"))
        self.assertFalse(asr.is_summary_valid_chinese("针对《暗店街》作者莫迪亚诺诺贝尔文学奖作品的简短说明文本"))


class RefresherLoopTest(unittest.TestCase):
    def test_loop_survives_a_database_error(self):
        steps = []

        def fail_once_then_stop(*_args):
            steps.append(1)
            if len(steps) == 1:
                raise database.sqlite3.OperationalError("database is locked")
            asr._stop_event.set()
            return 0.0

        asr._stop_event.clear()
        self.addCleanup(asr._stop_event.clear)
        with mock.patch.object(asr, "_refresh_next_book", fail_once_then_stop), \
             mock.patch.object(asr._stop_event, "wait", return_value=False):
            asr._refresher_loop(Path("unused.db"))
        self.assertEqual(len(steps), 2)


class AutoSummaryRefresherDbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        database.init_db(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_priority_pool_and_skip_logic(self):
        conn = database.get_conn(self.db_path)
        try:
            good_sum = "本书系统阐述现代编译器的设计与实现，涵盖词法分析、语法分析、类型检查与代码生成。"
            # Ignored: unread with no rating
            _insert_book(conn, "k1", "UnreadBook", summary="")
            # Ignored: already cleanly refined
            b_done = _insert_book(
                conn, "k2", "DoneFav", is_favorite=1, summary=good_sum,
                summary_source="ai_refreshed", target_audience="工程师", core_points='["要点1"]',
            )
            _insert_ai_meta(conn, b_done)
            # Picked: xlsx_curated even with target_audience populated
            b_xlsx = _insert_book(
                conn, "k2b", "XlsxFav", is_favorite=1, summary=good_sum,
                summary_source="xlsx_curated", target_audience="工程师", core_points='["要点1"]',
            )
            _insert_ai_meta(conn, b_xlsx)
            # Picked: has book_ai_meta but English-only summary
            b_en = _insert_book(
                conn, "k3", "EnglishSummaryWant", reading_status="want",
                summary="This book provides a comprehensive guide to convex optimization and duality theory.",
                summary_source="ai_refreshed", target_audience="Students", core_points='["A"]',
            )
            _insert_ai_meta(conn, b_en)
            # Picked: rated via book_external_meta
            b_ext = _insert_book(conn, "k4", "ExtRated", summary="")
            conn.execute(
                "INSERT INTO book_external_meta (book_id, source, external_id, rating, status) VALUES (?, 'google_books', 'g1', 8.5, 'success')",
                (b_ext,),
            )
            conn.commit()

            picked = asr.fetch_next_missing_target_book(conn)
            self.assertEqual(picked, b_xlsx)

            asr.mark_unreadable_for_auto_refresh(b_xlsx, db_path=self.db_path)
            self.assertFalse(asr.needs_ai_summary_refresh(conn, b_xlsx))

            picked2 = asr.fetch_next_missing_target_book(conn)
            self.assertEqual(picked2, b_en)

            asr.mark_unreadable_for_auto_refresh(b_en, db_path=self.db_path)
            picked_next = asr.fetch_next_missing_target_book(conn)
            self.assertEqual(picked_next, b_ext)
        finally:
            conn.close()

    def test_process_candidate_handles_unreadable_and_daily_cap(self):
        conn = database.get_conn(self.db_path)
        try:
            b1 = _insert_book(conn, "k5", "ScanPdf", reading_status="reading", summary="")
            b2 = _insert_book(conn, "k6", "CapHit", is_favorite=1, summary="")
        finally:
            conn.close()

        with mock.patch.object(bes, "refresh_book_ai_metadata", side_effect=bes.NoReadableTextError("no text")):
            ok = asr._process_candidate(b1, self.db_path)
            self.assertTrue(ok)

        conn = database.get_conn(self.db_path)
        try:
            self.assertFalse(asr.needs_ai_summary_refresh(conn, b1))
        finally:
            conn.close()

        with mock.patch.object(bes, "refresh_book_ai_metadata", side_effect=ds.DailyCapReached("cap")):
            ok2 = asr._process_candidate(b2, self.db_path)
            self.assertFalse(ok2)

    def test_enqueue_deduplicates(self):
        with asr._queued_lock:
            asr._queued_ids.clear()
        while not asr._queue.empty():
            asr._queue.get_nowait()
        self.assertTrue(asr.enqueue_book_summary_refresh(42))
        self.assertFalse(asr.enqueue_book_summary_refresh(42))
        self.assertEqual(asr._queue.get_nowait(), 42)


if __name__ == "__main__":
    unittest.main()
