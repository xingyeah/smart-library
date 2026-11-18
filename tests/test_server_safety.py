"""Audit fixes: cross-site requests, rollback of last copies, range reads, scheduler clock, 500s, name clashes,
reader sanitizer, new-book enrichment. Temp DB and files only."""

import http.client
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import archiver  # noqa: E402
import classic_scorer  # noqa: E402
import database  # noqa: E402
import http_api  # noqa: E402
from http_api import background, gate, responses, state  # noqa: E402
from http_api.handlers import books, organize, reading, recommend, translate  # noqa: E402
import jobs  # noqa: E402
import reader  # noqa: E402
import server  # noqa: E402


class TempDbCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "t.db"
        database.init_db(self.db_path)

        _real_get_conn = database.get_conn

        def conn(*args, **kwargs):
            return _real_get_conn(self.db_path)

        for mod in (
            server,
            archiver,
            database,
            background,
            books,
            reading,
            organize,
            recommend,
        ):
            p = mock.patch.object(mod, "get_conn", conn)
            p.start()
            self.addCleanup(p.stop)


class HttpTest(TempDbCase):
    def setUp(self):
        super().setUp()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.LibraryRequestHandler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def request(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        return r, r.read()

    def test_cross_site_simple_post_rejected_and_no_cors(self):
        r, _ = self.request("POST", "/api/organize/rollback", b'{"run_id": 1}',
                            {"Content-Type": "text/plain", "Origin": "https://evil.example"})
        self.assertEqual(r.status, 415)
        self.assertIsNone(r.getheader("Access-Control-Allow-Origin"))
        r, _ = self.request("OPTIONS", "/api/organize/rollback", headers={"Origin": "https://evil.example"})
        self.assertGreaterEqual(r.status, 400)  # preflight denied
        self.assertIsNone(r.getheader("Access-Control-Allow-Origin"))
        r, _ = self.request("POST", "/api/nope", b"{}", {"Content-Type": "application/json"})
        self.assertEqual(r.status, 404)  # same-origin JSON still reaches the router

    def test_handler_error_returns_500_not_dropped_connection(self):
        with mock.patch.object(books, "instant_search", side_effect=RuntimeError("boom")):
            r, body = self.request("GET", "/api/books")
        self.assertEqual(r.status, 500)
        self.assertIn(b"internal server error", body)

    def test_lan_clients_need_token(self):
        with mock.patch.object(state, "_LOOPBACK", ()), mock.patch.object(state, "LAN_TOKEN", "secret-t"):
            post = lambda h: self.request("POST", "/api/nope", b"{}", {"Content-Type": "application/json", **h})[0].status
            self.assertEqual(post({}), 403)
            self.assertEqual(post({"Cookie": "lib_token=wrong"}), 403)
            self.assertEqual(post({"Cookie": "lib_token=secret-t"}), 404)  # passes the gate, reaches the router
            r, _ = self.request("GET", "/?token=secret-t")
            self.assertEqual((r.status, r.getheader("Location")), (302, "/"))
            r, _ = self.request("GET", "/?book=7&token=secret-t&q=a")
            self.assertEqual((r.status, r.getheader("Location")), (302, "/?book=7&q=a"))  # only the token is dropped
            self.assertIn("lib_token=secret-t", r.getheader("Set-Cookie"))
            self.assertIn("HttpOnly", r.getheader("Set-Cookie"))
            self.assertIn("SameSite=Lax", r.getheader("Set-Cookie"))  # links from other apps still carry it
            self.assertEqual(self.request("GET", "/?token=nope")[0].status, 403)
        with mock.patch.object(state, "_LOOPBACK", ()), mock.patch.object(state, "LAN_TOKEN", None):
            self.assertEqual(self.request("GET", "/?token=None")[0].status, 403)  # no token configured: deny

    def test_reading_status_only_known_values(self):
        with database.get_conn(self.db_path) as c:
            c.execute("INSERT INTO books (id, dedup_key, title, norm_title, primary_format, primary_path) VALUES (1, 'k', 't', 't', 'epub', '')")
        hdr = {"Content-Type": "application/json"}
        r, _ = self.request("POST", "/api/books/1/meta", b'{"reading_status": "x\');alert(1);(\'"}', hdr)
        self.assertEqual(r.status, 400)
        r, body = self.request("POST", "/api/books/1/meta", b'{"reading_status": "want"}', hdr)
        self.assertEqual(r.status, 200)
        with database.get_conn(self.db_path) as c:
            self.assertEqual(c.execute("SELECT reading_status FROM books WHERE id = 1").fetchone()[0], "want")

    def test_reading_progress_saved_and_older_records_never_win(self):
        with database.get_conn(self.db_path) as c:
            c.execute("INSERT INTO books (id, dedup_key, title, norm_title, primary_format, primary_path, reading_status) "
                      "VALUES (1, 'k', 't', 't', 'epub', '', 'want'), (2, 'k2', 't2', 't2', 'epub', '', 'read')")
        hdr = {"Content-Type": "application/json"}
        post = lambda bid, body: self.request("POST", f"/api/books/{bid}/progress", json.dumps(body).encode(), hdr)
        r, body = post(1, {"progress": 0.375, "chapter_idx": 3, "offset": 0.62, "label": "第四章"})
        self.assertEqual((r.status, json.loads(body)["updated"]), (200, 1))
        # An old browser record (2020) must not replace the newer position
        r, body = post(1, {"chapter_idx": 0, "label": "old", "opened_at": 1577836800000})
        self.assertEqual(json.loads(body)["updated"], 0)
        post(2, {"progress": 1.5, "chapter_idx": 9})  # clamped; a finished book keeps 已读
        self.assertEqual(post(1, {"progress": "x"})[0].status, 400)
        with database.get_conn(self.db_path) as c:
            rows = {r["id"]: r for r in c.execute("SELECT id, reading_status, read_progress, read_pos, last_opened_at FROM books")}
        self.assertEqual(rows[1]["reading_status"], "reading")
        self.assertEqual((rows[1]["read_progress"], json.loads(rows[1]["read_pos"])["offset"]), (0.375, 0.62))
        self.assertTrue(rows[1]["last_opened_at"])
        self.assertEqual((rows[2]["reading_status"], rows[2]["read_progress"]), ("read", 1.0))

    def test_conflict_update_refreshes_search_fields_and_vector(self):
        with database.get_conn(self.db_path) as c:
            c.execute("INSERT INTO books (id, dedup_key, title, norm_title, author, primary_format, primary_path, category, summary) "
                      "VALUES (1, 'k', 'santi_v2', 'santi_v2', '未知作者', 'epub', '', '文学小说与艺术', '科幻')")
            c.execute("INSERT INTO conflict_inbox (id, file_path, detected_title, detected_author, candidate_book_id, conflict_type) "
                      "VALUES (5, '/x.epub', 'santi_v2', '', 1, 'missing_author')")
        with mock.patch.object(organize, "spawn_reindex") as reindex:
            r, _ = self.request("POST", "/api/conflicts/5/resolve",
                                json.dumps({"action": "update_book", "title": "三体", "author": ""}).encode(),
                                {"Content-Type": "application/json"})
        self.assertEqual(r.status, 200)
        reindex.assert_called_once_with([1])
        with database.get_conn(self.db_path) as c:
            row = c.execute("SELECT title, author, norm_title, pinyin_search FROM books WHERE id = 1").fetchone()
            status = c.execute("SELECT status FROM conflict_inbox WHERE id = 5").fetchone()[0]
        self.assertEqual((row["title"], row["author"], status), ("三体", "未知作者", "resolved"))
        self.assertEqual(row["norm_title"], server.normalize_for_dedup("三体", "未知作者")[0])  # from the final title
        self.assertIn("santi", row["pinyin_search"])

    def test_range_reads_only_requested_bytes(self):
        f = Path(self.tmp.name) / "big.pdf"
        data = os.urandom(300_000)
        f.write_bytes(data)
        with database.get_conn(self.db_path) as c:
            c.execute("INSERT INTO books (id, dedup_key, title, norm_title, primary_format, primary_path, archived_path) "
                      "VALUES (1, 'k', 't', 't', 'pdf', ?, '')", (str(f),))
        reads = []
        real_open = open

        class Tracked:
            def __init__(self, f):
                self.f = f

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.f.close()

            def read(self, n=-1):
                reads.append(n)
                return self.f.read(n)

            def seek(self, *a):
                return self.f.seek(*a)

        with mock.patch.object(responses, "open", create=True, new=lambda p, *a, **k: Tracked(real_open(p, *a, **k))):
            r, body = self.request("GET", "/api/books/1/stream", headers={"Range": "bytes=1000-1099"})
            self.assertEqual((r.status, body, r.getheader("Content-Range")), (206, data[1000:1100], "bytes 1000-1099/300000"))
            r, body = self.request("GET", "/api/books/1/download")
            self.assertEqual((r.status, body), (200, data))
        self.assertTrue(reads and all(0 < n <= 1024 * 1024 for n in reads), f"unbounded reads: {reads}")  # never read()-all

    def test_range_suffix_and_malformed(self):
        f = Path(self.tmp.name) / "r.pdf"
        data = os.urandom(1000)
        f.write_bytes(data)
        with database.get_conn(self.db_path) as c:
            c.execute("INSERT INTO books (id, dedup_key, title, norm_title, primary_format, primary_path, archived_path) "
                      "VALUES (1, 'k', 'r', 'r', 'pdf', ?, '')", (str(f),))
        r, body = self.request("GET", "/api/books/1/stream", headers={"Range": "bytes=-100"})
        self.assertEqual((r.status, body, r.getheader("Content-Range")), (206, data[900:], "bytes 900-999/1000"))
        r, _ = self.request("GET", "/api/books/1/stream", headers={"Range": "bytes=abc-"})
        self.assertEqual(r.status, 416)

    def test_bad_page_param_returns_400(self):
        r, _ = self.request("GET", "/api/books?page=abc")
        self.assertEqual(r.status, 400)


class RollbackTest(TempDbCase):
    def test_keeps_link_when_source_was_deleted(self):
        lib = Path(self.tmp.name) / "Library"
        lib.mkdir()
        with mock.patch.object(archiver, "LIBRARY_DIR", lib):
            pairs = []
            for name in ("still_has_source", "only_copy"):
                src = Path(self.tmp.name) / f"{name}.epub"
                src.write_bytes(name.encode())
                dst = lib / f"{name}.epub"
                os.link(src, dst)
                pairs.append((src, dst))
            pairs[1][0].unlink()  # user cleaned the source folder after archiving
            with database.get_conn(self.db_path) as c:
                for src, dst in pairs:
                    c.execute("INSERT INTO file_ops_log (run_id, op_type, src_path, dst_path, book_id) "
                              "VALUES (7, 'hardlink_archive', ?, ?, 1)", (str(src), str(dst)))
            res = archiver.rollback_organize_run(7)
        self.assertEqual((res["unlinked_files"], res["kept_last_copies"]), (1, 1))
        self.assertFalse(pairs[0][1].exists())
        self.assertEqual(pairs[1][1].read_bytes(), b"only_copy")


class ArchiveLinkTest(TempDbCase):
    def test_same_name_different_book_keeps_both(self):
        lib = Path(self.tmp.name) / "Library"
        with mock.patch.object(archiver, "LIBRARY_DIR", lib):
            paths = []
            for i in (1, 2):
                src = Path(self.tmp.name) / f"src{i}.epub"
                src.write_bytes(f"book {i}".encode())
                paths.append(archiver.safe_hardlink_to_library(str(src), "A" * 60, "Same", "", "", "epub"))
            again = archiver.safe_hardlink_to_library(str(Path(self.tmp.name) / "src1.epub"), "A" * 60, "Same", "", "", "epub")
        self.assertNotEqual(paths[0], paths[1])
        self.assertEqual([Path(p).read_bytes() for p in paths], [b"book 1", b"book 2"])
        self.assertEqual(again, paths[0])  # re-archiving the same file reuses its link

    def test_new_books_enriched_only_with_key(self):
        with mock.patch("deepseek_client.api_key", return_value=None), mock.patch("enrich_ai.cmd_run") as run:
            archiver._enrich_new_books([1, 2])
        run.assert_not_called()
        with mock.patch("deepseek_client.api_key", return_value="k"), mock.patch("enrich_ai.cmd_run") as run:
            archiver._enrich_new_books([])
            run.assert_not_called()
            archiver._enrich_new_books(list(range(1200)))
        self.assertEqual([len(c.kwargs["ids"]) for c in run.call_args_list], [500, 500, 200])


class DoubanFetchTest(unittest.TestCase):
    def test_network_errors_raise_not_found_returns_none(self):
        import urllib.error

        with mock.patch.object(classic_scorer.urllib.request, "urlopen", side_effect=urllib.error.URLError("offline")):
            with self.assertRaises(OSError):
                classic_scorer._fetch_douban_rating_for_book("三体", "刘慈欣", "")
        err = urllib.error.HTTPError("u", 429, "Too Many", {}, None)
        with mock.patch.object(classic_scorer.urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(OSError):
                classic_scorer._fetch_douban_rating_for_book("三体", "刘慈欣", "")
        err404 = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        with mock.patch.object(classic_scorer.urllib.request, "urlopen", side_effect=err404):
            self.assertIsNone(classic_scorer._fetch_douban_rating_for_book("三体", "刘慈欣", ""))


class ReaderChapterListTest(unittest.TestCase):
    def test_lengths_from_epub_count_or_html(self):
        chapters = [{"index": 0, "title": "a", "chars": 120, "file": "x"}, {"index": 1, "title": "b", "html": "<p>hello <b>world</b></p>"}]
        self.assertEqual(reader._chapter_list(chapters),
                         [{"index": 0, "title": "a", "chars": 120}, {"index": 1, "title": "b", "chars": len("hello world")}])


class HostileEbookTest(unittest.TestCase):
    def test_entity_xml_and_oversized_members_refused(self):
        import io
        import zipfile

        import parser as ebook_parser

        bomb = b'<?xml version="1.0"?><!DOCTYPE p [<!ENTITY a "x">]><package>&a;</package>'
        with self.assertRaises(ValueError):
            ebook_parser.safe_xml(bomb)
        with self.assertRaises(ValueError):
            ebook_parser.safe_xml(bomb.decode().encode("utf-16"))  # NUL bytes between letters
        self.assertEqual(ebook_parser.safe_xml(b"<package><x>ok</x></package>").find("x").text, "ok")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("big.xhtml", b"0" * 2_000_000)  # 2 MB inflated, a few KB compressed
            zf.writestr("small.xhtml", b"hi")
        with zipfile.ZipFile(buf) as zf:
            with self.assertRaises(ValueError):
                ebook_parser.safe_zip_read(zf, "big.xhtml", cap=1_000_000)
            self.assertEqual(ebook_parser.safe_zip_read(zf, "small.xhtml"), b"hi")


class DoubanBonusTest(unittest.TestCase):
    def test_enricher_and_full_rescore_agree(self):
        base = {"title": "t", "author": "a", "category": "", "tags": "[]", "summary": "", "pub_year": "", "douban_rating": 0}
        before, _ = classic_scorer.compute_book_classic_Signals(base, 1, 1, 1, 1)
        after, _ = classic_scorer.compute_book_classic_Signals({**base, "douban_rating": 9.0, "douban_votes": 10000}, 1, 1, 1, 1)
        self.assertAlmostEqual(after - before, classic_scorer.douban_bonus(9.0, 10000), places=1)
        self.assertEqual(classic_scorer.douban_bonus(4.0, 999), 0.0)


class ReaderSanitizeTest(unittest.TestCase):
    def test_attributes_on_any_tag_are_rebuilt(self):
        payloads = [
            '<p>x<i/onmouseover=alert(1)>y</i></p>',          # no space before on*
            '<em a="1"onclick=alert(2)>z</em>',               # attribute glued to the previous one
            '<figure/onmouseover=alert(3)>f</figure>',
            '<b style="background:url(http://evil/x.png)">s</b>',  # tracking pixel via CSS
            '<i title=">" onmouseover=alert(4)>t</i>',
        ]
        for raw in payloads:
            out = reader.sanitize_and_format_book_html(raw, 1).lower()
            self.assertNotRegex(out, r"<[^>]*\bon[a-z]+\s*=", raw)
            self.assertNotIn("url(", out, raw)
        kept = reader.sanitize_and_format_book_html('<p id="c1" style="text-align:center">中</p><a href="#n1">注</a>', 1)
        self.assertIn('id="c1"', kept)
        self.assertIn("text-align:center", kept)
        self.assertIn("scrollToReaderAnchor", kept)  # our own internal-link handler survives

    def test_unclosed_active_tags_removed(self):
        out = reader.sanitize_and_format_book_html(
            '<p>hi</p><iframe src="https://evil.example"><form action="https://evil.example"><base href="https://x">', 1
        )
        self.assertIn("hi", out)
        for tag in ("<iframe", "<form", "<base"):
            self.assertNotIn(tag, out.lower())

    def test_js_string_escapes_for_attribute(self):
        self.assertEqual(reader._js_string("x';alert(1)//"), "&quot;x&#x27;;alert(1)//&quot;")

    def test_internal_link_fragment_cannot_break_out(self):
        out = reader.sanitize_and_format_book_html('<a href="#x&#x27;);alert(1)//">link</a>', 1)
        # The fragment must land inside a double-quoted JS string, not a single-quoted one.
        self.assertIn("scrollToReaderAnchor(&quot;", out)
        self.assertNotIn("scrollToReaderAnchor('", out)

    def test_interactive_tags_and_event_handlers_stripped(self):
        out = reader.sanitize_and_format_book_html(
            '<p>hi</p><video src="x" onerror="alert(1)"></video>'
            '<input autofocus onfocus="alert(1)">'
            '<b onclick="alert(1)">bold</b>'
            '<details open ontoggle="alert(1)"><summary>x</summary></details>', 1
        )
        self.assertIn("hi", out)
        self.assertIn("bold", out)
        for bad in ("<video", "<input", "<details", "onerror", "onclick", "onfocus", "ontoggle"):
            self.assertNotIn(bad, out.lower())

    def test_external_image_src_removed(self):
        out = reader.sanitize_and_format_book_html('<img src="https://evil.example/tracker.png">', 1)
        self.assertNotIn("evil.example", out)


class SchedulerTest(TempDbCase):
    def test_empty_last_run_starts_clock_then_runs_after_interval(self):
        with database.get_conn(self.db_path) as c:
            c.execute("INSERT OR IGNORE INTO user_interest_config (id) VALUES (1)")
            c.execute("UPDATE user_interest_config SET schedule_enabled = 1, schedule_interval_hours = 24, last_scheduled_at = '' WHERE id = 1")
        with mock.patch.object(background, "start_background_organize") as start:
            self.assertFalse(server._scheduler_tick(1000.0))  # first check only records the time
            self.assertFalse(server._scheduler_tick(1000.0 + 3600))
            self.assertTrue(server._scheduler_tick(1000.0 + 24 * 3600))
        start.assert_called_once()


class FtsTriggerTest(TempDbCase):
    def test_update_trigger_gated_to_indexed_columns(self):
        conn = database.get_conn(self.db_path)
        try:
            trigger_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='books_au'"
            ).fetchone()["sql"]
            self.assertIn("WHEN", trigger_sql.upper())

            conn.execute(
                "INSERT INTO books (dedup_key, title, norm_title, author) VALUES (?, ?, ?, ?)",
                ("k1", "Original", "original", "Author"),
            )
            conn.commit()
            bid = conn.execute("SELECT id FROM books WHERE dedup_key = 'k1'").fetchone()["id"]

            conn.execute("UPDATE books SET title = 'New Title' WHERE id = ?", (bid,))
            conn.commit()
            self.assertEqual(
                conn.execute("SELECT title FROM books_fts WHERE rowid = ?", (bid,)).fetchone()["title"],
                "New Title",
            )
        finally:
            conn.close()


class ReindexTest(unittest.TestCase):
    def test_background_reindex_calls_rebuild(self):
        with mock.patch.object(jobs, "rebuild_vector_cache") as rebuild:
            server._reindex_in_background([7])
        rebuild.assert_called_once_with(reencode_ids=[7])


if __name__ == "__main__":
    unittest.main()
