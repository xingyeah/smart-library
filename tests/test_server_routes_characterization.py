import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Add backend directory to path
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import archiver
import database
import book_enrichment_service
import deepseek_client
import enrich_ai
import server
import translator


from http_api import background, gate, responses, state
from http_api.handlers import books, organize, reading, recommend, translate


class MockSocket:
    """纯内存模拟 socket，避免沙箱环境下的 TCP 端口绑定与权限报错"""

    def __init__(self, request_bytes: bytes):
        self.rfile = io.BytesIO(request_bytes)
        self.wfile = io.BytesIO()

    def settimeout(self, timeout):
        """The handler sets a socket timeout on every connection; an in-memory socket never blocks."""

    def makefile(self, mode: str, *args, **kwargs):
        if "b" in mode:
            if "r" in mode:
                return self.rfile
            return self.wfile
        return self.rfile

    def sendall(self, b: bytes):
        self.wfile.write(b)


class MockServer:
    server_address = ("127.0.0.1", 8080)


class TestServerRoutesCharacterization(unittest.TestCase):
    """
    阶段零补充特征测试：覆盖现有安全测试中未涉及的 server._handle_get / _handle_post 核心业务路由。
    为阶段二 HTTP 服务端解耦（http_api/）提供行为不变的坚实安全网。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "test_server.db"
        database.init_db(self.db_path)

        _real_get_conn = database.get_conn

        def conn(*args, **kwargs):
            return _real_get_conn(self.db_path)

        self._patches = [
            mock.patch.object(mod, "get_conn", conn)
            for mod in (
                server,
                archiver,
                database,
                background,
                books,
                reading,
                organize,
                recommend,
                translator,
                # Imported lazily by the handler: unpatched, it would bind whichever get_conn was live at first import
                book_enrichment_service,
            )
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)
        # Tests never reach external book sites; the cover fetch has its own test below
        no_net = mock.patch.object(book_enrichment_service, "_start_cover_fetch")
        no_net.start()
        self.addCleanup(no_net.stop)
        # A summary edit schedules a catalog rewrite of the real library; the timer thread dies with the test process
        # mid-write and leaves .电子书分类索引与简介目录.xlsx.<pid>.tmp behind in the repo
        no_catalog = mock.patch("catalog_sync.enqueue_catalog_sync")
        no_catalog.start()
        self.addCleanup(no_catalog.stop)

        # 预置 1 本测试书籍
        c = conn()
        cur = c.execute(
            """
            INSERT INTO books (
                dedup_key, title, subtitle, norm_title, author, norm_author,
                primary_format, primary_path, archived_path, file_size, file_hash,
                category, tags, language, quality_score, classic_score,
                pinyin_search, user_rating, is_favorite, summary
            ) VALUES (
                'test::book', '思考快与慢', '', '思考快与慢', '丹尼尔·卡尼曼', '丹尼尔卡尼曼',
                'epub', '/tmp/mock.epub', '/tmp/mock.epub', 1024, 'hash_123',
                '心理学与脑科学', '["心理学", "认知", "决策"]', 'zh-CN', 90.0, 50.0,
                'si kao kuai yu man', 0, 0, '关于思考系统一与系统二'
            )
            """
        )
        self.test_book_id = cur.lastrowid
        c.commit()

    def simulate_request(
        self, method: str, path: str, body: bytes = b"", headers: dict = None
    ):
        h = {"Host": "127.0.0.1"}
        if headers:
            h.update(headers)
        if body and "Content-Length" not in h:
            h["Content-Length"] = str(len(body))
        if body and "Content-Type" not in h:
            h["Content-Type"] = "application/json"

        req_lines = [f"{method} {path} HTTP/1.0"]
        for k, v in h.items():
            req_lines.append(f"{k}: {v}")
        req_lines.append("")
        req_bytes = "\r\n".join(req_lines).encode("utf-8") + b"\r\n" + body

        sock = MockSocket(req_bytes)
        server.LibraryRequestHandler(sock, ("127.0.0.1", 54321), MockServer())
        raw_resp = sock.wfile.getvalue()

        header_part, _, body_part = raw_resp.partition(b"\r\n\r\n")
        lines = header_part.split(b"\r\n")
        status_line = lines[0].decode("utf-8")
        status_code = int(status_line.split()[1])
        resp_headers = {}
        for line in lines[1:]:
            if b":" in line:
                k, v = line.split(b":", 1)
                resp_headers[k.decode("utf-8").strip().lower()] = v.decode("utf-8").strip()

        return status_code, resp_headers, body_part

    # ==========================================
    # GET 路由特征固化
    # ==========================================

    def test_get_stats(self):
        """GET /api/stats 返回全库总数、分类聚合、格式分布等统计"""
        status, _, body = self.simulate_request("GET", "/api/stats")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("total_books", data)
        self.assertGreaterEqual(data["total_books"], 1)
        self.assertIn("categories", data)
        self.assertIn("formats", data)

    def test_get_organize_status(self):
        """GET /api/organize/status 返回后台整理任务状态"""
        status, _, body = self.simulate_request("GET", "/api/organize/status")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("running", data)
        self.assertIn("progress", data)

    def test_get_recommend_interests(self):
        """GET /api/recommend/interests 返回冷启动或偏好推荐候选集"""
        status, _, body = self.simulate_request("GET", "/api/recommend/interests")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("items", data)

    def test_get_ai_usage(self):
        """GET /api/ai/usage 返回 Token 消耗与额度监控"""
        status, _, body = self.simulate_request("GET", "/api/ai/usage")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("today", data)
        self.assertIn("totals", data)
        self.assertIn("configured", data)

    def test_get_conflicts(self):
        """GET /api/conflicts 返回冲突收件箱列表"""
        status, _, body = self.simulate_request("GET", "/api/conflicts")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("items", data)

    # ==========================================
    # POST 路由特征固化
    # ==========================================

    def test_post_book_meta_favorite(self):
        """POST /api/books/{id}/meta 切换标星收藏状态"""
        payload = json.dumps({"is_favorite": 1}).encode("utf-8")
        status, _, body = self.simulate_request(
            "POST", f"/api/books/{self.test_book_id}/meta", body=payload
        )
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data.get("is_favorite"), 1)

        # 验证数据库状态已变更
        c = database.get_conn(self.db_path)
        row = c.execute("SELECT is_favorite FROM books WHERE id = ?", (self.test_book_id,)).fetchone()
        self.assertEqual(row["is_favorite"], 1)

    def test_post_book_meta_rating(self):
        """POST /api/books/{id}/meta 为图书打分"""
        payload = json.dumps({"user_rating": 5}).encode("utf-8")
        status, _, body = self.simulate_request(
            "POST", f"/api/books/{self.test_book_id}/meta", body=payload
        )
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data.get("user_rating"), 5)

        # 验证数据库评分已保存
        c = database.get_conn(self.db_path)
        row = c.execute("SELECT user_rating FROM books WHERE id = ?", (self.test_book_id,)).fetchone()
        self.assertEqual(row["user_rating"], 5)

    def test_post_book_meta_reading_status(self):
        """POST /api/books/{id}/meta 变更阅读状态（unread/want/reading/read）"""
        payload = json.dumps({"reading_status": "reading"}).encode("utf-8")
        status, _, body = self.simulate_request(
            "POST", f"/api/books/{self.test_book_id}/meta", body=payload
        )
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data.get("reading_status"), "reading")

    def test_post_book_progress(self):
        """POST /api/books/{id}/progress 记录阅读进度"""
        payload = json.dumps({
            "progress": 0.45,
            "chapter_idx": 2,
            "offset": 0.12,
            "label": "第三章 启发式思考",
        }).encode("utf-8")
        status, _, body = self.simulate_request(
            "POST", f"/api/books/{self.test_book_id}/progress", body=payload
        )
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("updated"), 1)

    def test_post_interests_config(self):
        """POST /api/interests/config 保存用户偏好分类配置并返回推荐"""
        payload = json.dumps({
            "preferred_categories": ["心理学与脑科学"],
            "exploration_ratio": 0.3,
        }).encode("utf-8")
        status, _, body = self.simulate_request(
            "POST", "/api/interests/config", body=payload
        )
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("items", data)

    def test_post_conflicts_resolve_ignore(self):
        """POST /api/conflicts/{id}/resolve 标记冲突项为 resolved"""
        c = database.get_conn(self.db_path)
        cur = c.execute(
            """
            INSERT INTO conflict_inbox (
                file_path, source_name, format, file_size, detected_title,
                conflict_type, conflict_detail, status
            ) VALUES (
                '/tmp/test.epub', 'Inbox', 'epub', 1024, '测试冲突书',
                'ambiguous_duplicate', '测试冲突细节', 'pending'
            )
            """
        )
        conflict_id = cur.lastrowid
        c.commit()

        payload = json.dumps({"action": "ignore"}).encode("utf-8")
        status, _, body = self.simulate_request(
            "POST", f"/api/conflicts/{conflict_id}/resolve", body=payload
        )
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("id"), conflict_id)

        # 验证状态变更为 resolved
        row = c.execute("SELECT status FROM conflict_inbox WHERE id = ?", (conflict_id,)).fetchone()
        self.assertEqual(row["status"], "resolved")

    @mock.patch("book_enrichment_service._extract_sample_content", return_value="Chapter 1. Variables and abstraction in mathematics.")
    @mock.patch("book_enrichment_service.refresh_book_vector_in_memory", return_value=True)
    @mock.patch("book_enrichment_service.spawn_reindex")
    @mock.patch("deepseek_client.chat")
    def test_post_book_enrich_refresh(self, mock_chat, mock_spawn, _mem_refresh, _sample):
        """POST /api/books/{id}/enrich_refresh 触发单书 AI 重新提炼与元数据修正"""
        mock_chat.return_value = (
            json.dumps({
                "clean_title": "数学导论 (Introduction to Mathematics)",
                "category": "数学与统计",
                "summary": "真实的数学哲学经典，探讨数学抽象本质与变量。",
                "core_points": ["数学的抽象本质", "变量与动力学"],
                "target_audience": "数学与哲学爱好者",
                "keywords": ["数学", "怀特海", "数学导论"],
                "level": "进阶",
                "kind": "学术理论",
                "subfield": "数学哲学",
            }),
            {"total_tokens": 100},
        )

        c = database.get_conn(self.db_path)
        cur = c.execute(
            """
            INSERT INTO books (
                dedup_key, title, norm_title, author, norm_author, category,
                summary, summary_source, primary_format, primary_path, quality_score
            ) VALUES (
                'test_enrich_key', '数学导论 (Barnes & Noble Series)', '数学导论',
                'Alfred North Whitehead', 'alfred north whitehead', '心理学与脑科学',
                '错配的心理学简介', 'xlsx_curated', 'epub', '/tmp/test.epub', 80.0
            )
            """
        )
        bid = cur.lastrowid
        c.commit()

        status, _, body = self.simulate_request("POST", f"/api/books/{bid}/enrich_refresh", body=b"{}")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data.get("ok"))
        # AI 不直接改书名：改写过的书名作为建议返回，等用户确认
        self.assertEqual(data["book"]["title"], "数学导论 (Barnes & Noble Series)")
        self.assertEqual(data["book"]["clean_title"], "数学导论 (Barnes & Noble Series)")
        self.assertEqual(data["enriched"]["proposed_title"], "数学导论 (Introduction to Mathematics)")
        self.assertEqual(data["book"]["category"], "数学与统计")

        # 验证数据库持久化已更新
        row = c.execute("SELECT * FROM books WHERE id = ?", (bid,)).fetchone()
        self.assertEqual(row["title"], "数学导论 (Barnes & Noble Series)")
        self.assertEqual(row["category"], "数学与统计")
        self.assertEqual(row["summary"], "真实的数学哲学经典，探讨数学抽象本质与变量。")
        self.assertEqual(row["summary_source"], "ai_refreshed")

        ai_row = c.execute("SELECT * FROM book_ai_meta WHERE book_id = ?", (bid,)).fetchone()
        self.assertIsNotNone(ai_row)
        self.assertEqual(ai_row["ai_category"], "数学与统计")
        # Paid call is logged under a capped feature, and the row is marked current for the batch enricher
        self.assertEqual(mock_chat.call_args.kwargs["feature"], "refresh")
        self.assertIn("refresh", deepseek_client.CAPPED_FEATURES)
        self.assertEqual(ai_row["input_hash"], enrich_ai.input_hash(enrich_ai.book_input(row)))
        self.assertEqual(ai_row["version"], enrich_ai.VERSION)
        self.assertIn("数学", row["pinyin_search"].replace(" ", ""))  # category and summary are searchable too
        self.assertNotIn("/tmp", mock_chat.call_args.kwargs["messages"][1]["content"])  # file name only, never the path

    def _insert_book(self, title="原书名", summary="原简介"):
        c = database.get_conn(self.db_path)
        cur = c.execute(
            "INSERT INTO books (dedup_key, title, norm_title, author, category, summary, primary_format, primary_path) "
            "VALUES (?, ?, ?, 'a', '心理学与脑科学', ?, 'epub', '/tmp/none.epub')",
            (f"k-{title}", title, title, summary),
        )
        c.commit()
        return c, cur.lastrowid

    def test_enrich_refresh_failures_leave_the_book_untouched(self):
        c, bid = self._insert_book()
        unchanged = lambda: tuple(c.execute("SELECT title, summary, category FROM books WHERE id = ?", (bid,)).fetchone())
        before = unchanged()
        with mock.patch("deepseek_client.chat") as chat:
            # the file cannot be read: no text, so no paid call and no overwrite
            status, _, body = self.simulate_request("POST", f"/api/books/{bid}/enrich_refresh", body=b"{}")
            self.assertEqual((status, unchanged()), (422, before))
            chat.assert_not_called()
            self.assertEqual(self.simulate_request("POST", "/api/books/999999/enrich_refresh", body=b"{}")[0], 404)
        with mock.patch("book_enrichment_service._extract_sample_content", return_value="some real text"), \
                mock.patch("deepseek_client.chat", side_effect=deepseek_client.DailyCapReached("cap")):
            status, _, body = self.simulate_request("POST", f"/api/books/{bid}/enrich_refresh", body=b"{}")
        self.assertEqual((status, unchanged()), (429, before))
        with mock.patch("book_enrichment_service._extract_sample_content", return_value="some real text"), \
                mock.patch("deepseek_client.chat", side_effect=OSError("api.deepseek.com timed out")):
            status, _, body = self.simulate_request("POST", f"/api/books/{bid}/enrich_refresh", body=b"{}")
        self.assertEqual((status, unchanged()), (504, before))  # a timeout is reported as such since b357436

    def test_refresh_cover_fetch_writes_only_the_cover(self):
        c, bid = self._insert_book(summary="短简介")
        c.execute("UPDATE books SET classic_score = 50 WHERE id = ?", (bid,))
        c.commit()
        row = c.execute("SELECT * FROM books WHERE id = ?", (bid,)).fetchone()
        found = mock.MagicMock(status="success", cover_url="http://x/c.jpg", source="douban", external_id="1",
                               description="另一本书的很长的简介" * 10, rating=9.5, votes=10000)
        with mock.patch("enrichers.scheduler.MultiSourceSlowEnricher._attempt_channel_fetch", return_value=found), \
                mock.patch("enrichers.scheduler._download_channel_cover", return_value="/covers/ext_douban_1.jpg"):
            book_enrichment_service._fetch_missing_cover(bid, row)
            book_enrichment_service._fetch_missing_cover(bid, row)  # a second refresh changes nothing more
        after = c.execute("SELECT cover_path, summary, classic_score FROM books WHERE id = ?", (bid,)).fetchone()
        self.assertEqual(tuple(after), ("/covers/ext_douban_1.jpg", "短简介", 50.0))

    def test_refresh_cover_fetch_skips_unanswered_channel(self):
        from enrichers.scheduler import CHANNEL_UNANSWERED
        c, bid = self._insert_book(summary="短简介")
        row = c.execute("SELECT * FROM books WHERE id = ?", (bid,)).fetchone()
        found = mock.MagicMock(status="success", cover_url="http://x/c.jpg", source="google_books", external_id="g1")
        with mock.patch("enrichers.scheduler.MultiSourceSlowEnricher._attempt_channel_fetch", side_effect=[CHANNEL_UNANSWERED, found]), \
                mock.patch("enrichers.scheduler._download_channel_cover", return_value="/covers/ext_gb_g1.jpg"):
            book_enrichment_service._fetch_missing_cover(bid, row)
        cover = c.execute("SELECT cover_path FROM books WHERE id = ?", (bid,)).fetchone()[0]
        self.assertEqual(cover, "/covers/ext_gb_g1.jpg")

    def test_excluded_categories_are_saved_trimmed_and_a_non_list_is_refused(self):
        def post(value):
            payload = json.dumps({"excluded_categories": value}, ensure_ascii=False).encode("utf-8")
            with mock.patch.object(recommend, "recommend_by_interests", return_value={"items": []}):
                return self.simulate_request("POST", "/api/interests/config", body=payload)[0]

        def stored():
            conn = database.get_conn(self.db_path)
            try:
                return json.loads(conn.execute("SELECT excluded_categories FROM user_interest_config").fetchone()[0])
            finally:
                conn.close()

        self.assertEqual(post([" 烹饪 ", "", "历史"]), 200)
        self.assertEqual(stored(), ["历史", "烹饪"])
        self.assertEqual(post("烹饪"), 400)
        self.assertEqual(post([1, 2]), 400)
        self.assertEqual(stored(), ["历史", "烹饪"])

    def test_exploration_ratio_is_stored_within_its_range_and_a_non_number_is_refused(self):
        def post(value):
            payload = json.dumps({"exploration_ratio": value}).encode("utf-8")
            with mock.patch.object(recommend, "recommend_by_interests", return_value={"items": []}):
                return self.simulate_request("POST", "/api/interests/config", body=payload)[0]

        def stored():
            conn = database.get_conn(self.db_path)
            try:
                return conn.execute("SELECT exploration_ratio FROM user_interest_config").fetchone()[0]
            finally:
                conn.close()

        self.assertEqual((post(0.3), stored()), (200, 0.3))
        self.assertEqual((post(7), stored()), (200, 0.5))
        self.assertEqual((post(-1), stored()), (200, 0.0))
        self.assertEqual(post("abc"), 400)
        self.assertEqual(post([0.2]), 400)
        self.assertEqual(stored(), 0.0)

    def test_ai_picks_choose_among_candidates_without_exploration(self):
        with mock.patch.object(recommend, "interest_candidates", return_value={"items": []}) as candidates, \
                mock.patch.object(recommend.ai_search, "pick_books", return_value=([], "empty")):
            status, _, _ = self.simulate_request("POST", "/api/recommend/interests/ai", body=b"{}")
        self.assertEqual(status, 200)
        self.assertEqual(candidates.call_args.kwargs["top_k"], 100)

    LONG_AGO = "2020-01-01 00:00:00"

    def _post_meta(self, book_id, **fields):
        with mock.patch.object(books, "spawn_reindex"), mock.patch.object(books, "enqueue_book_summary_refresh"):
            payload = json.dumps(fields, ensure_ascii=False).encode("utf-8")
            return self.simulate_request("POST", f"/api/books/{book_id}/meta", body=payload)[0]

    def _marked_at(self, conn, book_id, reset_to=None):
        """The book's marked_at; with `reset_to`, sets it first."""
        if reset_to is not None:
            conn.execute("UPDATE books SET marked_at = ? WHERE id = ?", (reset_to, book_id))
            conn.commit()
        return conn.execute("SELECT marked_at FROM books WHERE id = ?", (book_id,)).fetchone()[0]

    def test_each_kind_of_mark_records_when_it_was_made(self):
        c, bid = self._insert_book()
        for mark in ({"is_favorite": True}, {"reading_status": "want"}, {"user_rating": 5}):
            self._marked_at(c, bid, reset_to=self.LONG_AGO)
            self.assertEqual(self._post_meta(bid, **mark), 200)
            self.assertNotEqual(self._marked_at(c, bid), self.LONG_AGO, mark)

    def test_edit_of_a_books_details_is_not_a_mark(self):
        c, bid = self._insert_book()
        self.assertEqual(self._post_meta(bid, title="改过的书名", user_notes="再想想"), 200)
        self.assertIsNone(self._marked_at(c, bid))

    def test_same_mark_sent_again_keeps_its_time(self):
        c, bid = self._insert_book()
        self._post_meta(bid, is_favorite=True, reading_status="want", user_rating=4)
        self._marked_at(c, bid, reset_to=self.LONG_AGO)
        self.assertEqual(self._post_meta(bid, is_favorite=True, reading_status="want", user_rating=4), 200)
        self.assertEqual(self._marked_at(c, bid), self.LONG_AGO)

    def test_taking_a_mark_away_keeps_the_time_of_the_last_mark(self):
        c, bid = self._insert_book()
        self._post_meta(bid, is_favorite=True, reading_status="read", user_rating=4)
        self._marked_at(c, bid, reset_to=self.LONG_AGO)
        self.assertEqual(self._post_meta(bid, is_favorite=False, reading_status="unread", user_rating=0), 200)
        self.assertEqual(self._marked_at(c, bid), self.LONG_AGO)

    def test_opening_an_unread_book_marks_it_as_reading_at_that_time(self):
        c, bid = self._insert_book()
        opened_ms = 1_790_000_000_000  # 2026-09-21 14:13:20 UTC
        payload = json.dumps({"progress": 0.2, "opened_at": opened_ms}).encode("utf-8")
        with mock.patch.object(books, "enqueue_book_summary_refresh"):
            self.assertEqual(self.simulate_request("POST", f"/api/books/{bid}/progress", body=payload)[0], 200)
        status = c.execute("SELECT reading_status FROM books WHERE id = ?", (bid,)).fetchone()[0]
        self.assertEqual((status, self._marked_at(c, bid)), ("reading", "2026-09-21 14:13:20"))

    def test_reading_on_in_a_book_already_being_read_is_no_new_mark(self):
        c, bid = self._insert_book()
        self._post_meta(bid, reading_status="reading")
        self._marked_at(c, bid, reset_to=self.LONG_AGO)
        with mock.patch.object(books, "enqueue_book_summary_refresh"):
            self.simulate_request("POST", f"/api/books/{bid}/progress", body=json.dumps({"progress": 0.5}).encode("utf-8"))
        self.assertEqual(self._marked_at(c, bid), self.LONG_AGO)

    def test_interest_prompt_is_saved(self):
        payload = json.dumps({"interest_prompt": "  明清史；分布式系统 ", "exploration_ratio": 0.3}).encode("utf-8")
        with mock.patch.object(recommend, "recommend_by_interests", return_value={"items": []}):
            status, _, _ = self.simulate_request("POST", "/api/interests/config", body=payload)
        self.assertEqual(status, 200)
        c = database.get_conn(self.db_path)
        self.assertEqual(c.execute("SELECT interest_prompt FROM user_interest_config WHERE id = 1").fetchone()[0], "明清史；分布式系统")

    def test_translation_gets_book_context_as_title_and_author(self):
        c, bid = self._insert_book(title="三体")
        with mock.patch.object(translate, "translate_text", return_value={"ok": True}) as tr:
            status, _, _ = self.simulate_request("POST", "/api/translate", body=json.dumps({"text": "hello", "book_id": bid}).encode())
        self.assertEqual(status, 200)
        context = tr.call_args.kwargs["context"]
        self.assertEqual(context, {"title": "三体", "author": "a"})
        self.assertIn("三体", translator._llm_user_prompt(["hello"], context))  # the shape the LLM engines read

    def test_paging_values_are_clamped_not_reset(self):
        with mock.patch.object(books, "search_shelf", return_value={"items": [], "total": 0}) as search:
            self.simulate_request("GET", "/api/books?page_size=500&page=0")
            self.assertEqual((search.call_args.args[0].page, search.call_args.args[0].page_size), (1, 200))
            self.simulate_request("GET", "/api/books?page_size=")
            self.assertEqual(search.call_args.args[0].page_size, 24)
            self.simulate_request("GET", "/api/books?author=Joe%20Navarro")
            self.assertEqual(search.call_args.args[0].filters.author, "Joe Navarro")
            self.assertEqual(self.simulate_request("GET", "/api/books?page=abc")[0], 400)
        with mock.patch.object(recommend, "recommend_by_interests", return_value={"items": []}) as rec:
            self.simulate_request("GET", "/api/recommend/interests")
            self.assertEqual(rec.call_args.kwargs["top_k"], 10)
            self.assertEqual(self.simulate_request("GET", "/api/recommend/interests?top_k=x")[0], 400)

    def test_lan_token_file_is_the_original_one_and_private(self):
        import config

        self.assertEqual(state.LAN_TOKEN_FILE, config.DATA_DIR / "lan_token")
        token_file = Path(self.tmp.name) / "lan_token"
        token = state.load_or_create_token(token_file)
        self.assertEqual(oct(token_file.stat().st_mode & 0o777), "0o600")
        self.assertEqual(state.load_or_create_token(token_file), token)  # stable across restarts

    def test_get_cover_static(self):
        """GET /covers/{filename} 能够正确读取 COVERS_DIR 中的图片"""
        import config
        test_cover_file = config.COVERS_DIR / "test_dummy_cover.jpg"
        test_cover_file.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
        try:
            status, headers, body = self.simulate_request("GET", "/covers/test_dummy_cover.jpg")
            self.assertEqual(status, 200)
            self.assertEqual(headers.get("content-type"), "image/jpeg")
            self.assertEqual(body, test_cover_file.read_bytes())

            # 测试 404
            status_404, _, _ = self.simulate_request("GET", "/covers/non_existent_cover.jpg")
            self.assertEqual(status_404, 404)
        finally:
            if test_cover_file.exists():
                test_cover_file.unlink()

    def test_get_book_detail_includes_external_meta(self):
        """GET /api/books/{id} 返回数据中包含外部多源评分与短评结构"""
        database.upsert_external_meta(
            self.db_path,
            book_id=self.test_book_id,
            source="douban",
            external_id="36890011",
            rating=8.6,
            raw_rating=8.6,
            votes=1200,
            description="心理学经典",
            reviews=[{"author": "读者小王", "rating": 5, "content": "非常实用！", "source": "douban"}],
            status="success",
        )
        status, _, body = self.simulate_request("GET", f"/api/books/{self.test_book_id}")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("external_meta", data)
        self.assertEqual(len(data["external_meta"]["ratings"]), 1)
        self.assertEqual(data["external_meta"]["ratings"][0]["source"], "douban")
        self.assertEqual(len(data["external_meta"]["reviews"]), 1)
        self.assertEqual(data["external_meta"]["reviews"][0]["content"], "非常实用！")
        self.assertNotIn("similar_books", data)

    def test_get_similar_books_has_its_own_route(self):
        """GET /api/books/{id}/similar 单独返回相似书，详情不再等向量检索"""
        neighbour = {"id": 2, "title": "相邻的书", "similarity": 71.2}
        with mock.patch.object(books, "get_similar_books", return_value=[neighbour]) as similar:
            status, _, body = self.simulate_request("GET", f"/api/books/{self.test_book_id}/similar")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body.decode("utf-8")), {"items": [neighbour]})
        similar.assert_called_once_with(self.test_book_id, top_k=6)


if __name__ == "__main__":
    unittest.main()
