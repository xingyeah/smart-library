import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import auto_summary_refresher as asr
import book_enrichment_service as bes
import build_bge_index
import database
import semantic_models
from http_api import responses
from http_api.handlers import reading
from http_api.server_handler import LibraryRequestHandler


class _RecordingWriter(io.BytesIO):
    def __init__(self) -> None:
        super().__init__()
        self.chunks = []

    def write(self, data):
        raw = bytes(data)
        self.chunks.append(raw)
        return super().write(raw)


class _DummyHandler:
    def __init__(self, headers=None) -> None:
        self.headers = headers or {}
        self.status = None
        self.sent_headers = {}
        self.error_code = None
        self.error_msg = None
        self.wfile = _RecordingWriter()

    def send_response(self, code: int) -> None:
        self.status = code

    def send_header(self, key: str, value: str) -> None:
        self.sent_headers[key] = value

    def end_headers(self) -> None:
        pass

    def send_error(self, code: int, message: str = "") -> None:
        self.error_code = code
        self.error_msg = message


def _insert_book(conn, dedup_key: str, title: str, **kwargs) -> int:
    cols = {
        "dedup_key": dedup_key,
        "title": title,
        "norm_title": title.lower(),
        "author": kwargs.get("author", "Tester"),
        "norm_author": "tester",
        "summary": kwargs.get("summary", ""),
        "primary_format": kwargs.get("primary_format", ""),
        "primary_path": kwargs.get("primary_path", ""),
        "archived_path": kwargs.get("archived_path", ""),
        "is_favorite": kwargs.get("is_favorite", 0),
        "is_deleted": kwargs.get("is_deleted", 0),
    }
    keys = ", ".join(cols.keys())
    placeholders = ", ".join("?" for _ in cols)
    cur = conn.execute(f"INSERT INTO books ({keys}) VALUES ({placeholders})", list(cols.values()))
    conn.commit()
    return int(cur.lastrowid)


class WriteTxTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "write_tx.db"
        database.init_db(self.db_path)

    def test_write_tx_commits_and_rolls_back(self) -> None:
        with database.write_tx(self.db_path) as conn:
            conn.execute(
                "INSERT INTO books (dedup_key, title, norm_title) VALUES ('k1', 'Book One', 'book one')"
            )

        with self.assertRaises(RuntimeError):
            with database.write_tx(conn_factory=lambda: database.get_conn(self.db_path)) as conn:
                conn.execute(
                    "INSERT INTO books (dedup_key, title, norm_title) VALUES ('k2', 'Book Two', 'book two')"
                )
                raise RuntimeError("abort tx")

        check_conn = database.get_conn(self.db_path)
        try:
            titles = [r["title"] for r in check_conn.execute("SELECT title FROM books ORDER BY id").fetchall()]
        finally:
            check_conn.close()
        self.assertEqual(titles, ["Book One"])


class BgeIndexStabilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "bge.db"
        self.npy_path = self.root / "bge_m3_vectors.npy"
        self.meta_path = self.root / "bge_m3_meta.json"
        database.init_db(self.db_path)

    def test_excludes_deleted_and_reencodes_changed_passage_hashes(self) -> None:
        conn = database.get_conn(self.db_path)
        try:
            bid1 = _insert_book(conn, "b1", "Alpha", summary="First edition summary")
            _insert_book(conn, "b2", "Deleted", summary="Should be skipped", is_deleted=1)
            bid3 = _insert_book(conn, "b3", "Beta", summary="Unchanged summary")
        finally:
            conn.close()

        encoded_batches = []

        class FakeEngine:
            def is_embedder_ready(self) -> bool:
                return True

            def encode_texts(self, texts, max_length=144, batch_size=8):
                encoded_batches.append(list(texts))
                vecs = np.zeros((len(texts), build_bge_index.EMBED_DIM), dtype=np.float32)
                for idx, _ in enumerate(texts):
                    vecs[idx, idx % build_bge_index.EMBED_DIM] = 1.0
                return vecs

            def load_vector_index(self) -> bool:
                return True

        fake_engine = FakeEngine()
        patches = [
            mock.patch.object(build_bge_index, "get_conn", lambda: database.get_conn(self.db_path)),
            mock.patch.object(build_bge_index, "BGE_VEC_NPY_PATH", self.npy_path),
            mock.patch.object(build_bge_index, "BGE_VEC_META_PATH", self.meta_path),
            mock.patch.object(build_bge_index, "get_bge_engine", lambda: fake_engine),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        first = build_bge_index.sync_bge_m3_index()
        self.assertTrue(first["ok"])
        self.assertEqual(first["total_books"], 2)
        self.assertEqual(first["newly_encoded"], 2)

        meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
        self.assertEqual(meta["book_ids"], [bid1, bid3])
        self.assertEqual(len(meta["passage_hashes"]), 2)
        self.assertFalse(self.npy_path.with_suffix(".tmp.npy").exists())
        self.assertFalse(self.meta_path.with_suffix(".tmp.json").exists())

        second = build_bge_index.sync_bge_m3_index()
        self.assertEqual(second["newly_encoded"], 0)

        conn = database.get_conn(self.db_path)
        try:
            conn.execute("UPDATE books SET summary = 'Updated edition summary' WHERE id = ?", (bid1,))
            conn.commit()
        finally:
            conn.close()

        third = build_bge_index.sync_bge_m3_index()
        self.assertEqual(third["newly_encoded"], 1)
        self.assertEqual(len(encoded_batches), 2)
        self.assertEqual(len(encoded_batches[1]), 1)
        self.assertIn("Updated edition summary", encoded_batches[1][0])


class SemanticModelsSnapTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.npy_path = self.root / "vecs.npy"
        self.meta_path = self.root / "meta.json"

        mat = np.zeros((3, semantic_models.EMBED_DIM), dtype=np.float32)
        mat[0, 0] = 1.0  # book 10
        mat[1, 1] = 1.0  # book 20
        mat[2, 2] = 1.0  # book 30
        np.save(str(self.npy_path), mat)
        self.meta_path.write_text(json.dumps({"book_ids": [10, 20, 30]}), encoding="utf-8")

    def test_dense_search_and_get_book_vectors_use_snap(self) -> None:
        with (
            mock.patch.object(semantic_models, "BGE_VEC_NPY_PATH", self.npy_path),
            mock.patch.object(semantic_models, "BGE_VEC_META_PATH", self.meta_path),
        ):
            engine = semantic_models.BgeM3Engine()
            q = np.zeros(semantic_models.EMBED_DIM, dtype=np.float32)
            q[1] = 0.9
            q[0] = 0.2
            q /= np.linalg.norm(q)
            engine.encode_query = lambda query: q

            scores, ranks = engine.dense_search("query", top_k=2)
            self.assertEqual(ranks, {20: 1, 10: 2})
            self.assertGreater(scores[20], scores[10])

            ids, vecs = engine.get_book_vectors([30, 999, 10])
            self.assertEqual(ids, [30, 10])
            self.assertEqual(vecs.shape, (2, semantic_models.EMBED_DIM))
            self.assertAlmostEqual(float(vecs[0, 2]), 1.0)
            self.assertAlmostEqual(float(vecs[1, 0]), 1.0)


class RangeStreamHttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bin_path = Path(self.tmp.name) / "sample.bin"
        self.payload = bytes(range(256))
        self.bin_path.write_bytes(self.payload)

    def test_range_stream_chunks_206_and_sets_content_length_on_416(self) -> None:
        self.assertEqual(LibraryRequestHandler.protocol_version, "HTTP/1.1")

        handler_206 = _DummyHandler()
        with mock.patch.object(responses, "RANGE_CHUNK_BYTES", 16):
            responses.send_range_stream(
                handler_206, str(self.bin_path), "bytes=10-59", "application/octet-stream"
            )
        self.assertEqual(handler_206.status, 206)
        self.assertEqual(handler_206.sent_headers["Content-Range"], "bytes 10-59/256")
        self.assertEqual(handler_206.sent_headers["Content-Length"], "50")
        self.assertGreater(len(handler_206.wfile.chunks), 1)
        self.assertTrue(all(len(c) <= 16 for c in handler_206.wfile.chunks))
        self.assertEqual(handler_206.wfile.getvalue(), self.payload[10:60])

        handler_416 = _DummyHandler()
        responses.send_range_stream(
            handler_416, str(self.bin_path), "bytes=999-1000", "application/octet-stream"
        )
        self.assertEqual(handler_416.status, 416)
        self.assertEqual(handler_416.sent_headers["Content-Range"], "bytes */256")
        self.assertEqual(handler_416.sent_headers["Content-Length"], "0")
        self.assertEqual(handler_416.wfile.getvalue(), b"")


class ReadingDeletedGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "reading.db"
        database.init_db(self.db_path)

        self.dummy_file = self.root / "book.pdf"
        self.dummy_file.write_bytes(b"%PDF-1.4\n")

        conn = database.get_conn(self.db_path)
        try:
            self.deleted_bid = _insert_book(
                conn,
                "del1",
                "Deleted Book",
                primary_format="pdf",
                primary_path=str(self.dummy_file),
                is_deleted=1,
            )
            cur = conn.execute(
                "INSERT INTO book_files (book_id, format, original_path) VALUES (?, 'pdf', ?)",
                (self.deleted_bid, str(self.dummy_file)),
            )
            conn.commit()
            self.file_id = int(cur.lastrowid)
        finally:
            conn.close()

    def test_deleted_book_returns_404_across_reading_routes(self) -> None:
        with mock.patch.object(reading, "get_conn", lambda: database.get_conn(self.db_path)):
            h_read = _DummyHandler()
            reading.handle_get_reader_content(h_read, {}, {}, str(self.deleted_bid))
            self.assertEqual(h_read.status, 404)

            h_epub = _DummyHandler()
            reading.handle_get_epub_asset(h_epub, {"path": ["chap1.xhtml"]}, {}, str(self.deleted_bid))
            self.assertEqual(h_epub.error_code, 404)

            h_mobi = _DummyHandler()
            reading.handle_get_mobi_image(h_mobi, {"idx": ["1"]}, {}, str(self.deleted_bid))
            self.assertEqual(h_mobi.error_code, 404)

            h_stream = _DummyHandler()
            reading.handle_get_book_file(h_stream, {}, {}, str(self.deleted_bid), "stream")
            self.assertEqual(h_stream.error_code, 404)

            h_dl = _DummyHandler()
            reading.handle_get_book_file(
                h_dl, {"file_id": [str(self.file_id)]}, {}, str(self.deleted_bid), "download"
            )
            self.assertEqual(h_dl.error_code, 404)


class AutoSummaryBackoffTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "backoff.db"
        database.init_db(self.db_path)
        self._clear_backoff()
        self.addCleanup(self._clear_backoff)

    @staticmethod
    def _clear_backoff() -> None:
        with asr._backoff_lock:
            asr._failed_until.clear()

    def test_unexpected_error_puts_book_in_backoff(self) -> None:
        conn = database.get_conn(self.db_path)
        try:
            bid = _insert_book(conn, "fav1", "Priority Missing Summary", is_favorite=1, summary="")
        finally:
            conn.close()

        with mock.patch.object(bes, "refresh_book_ai_metadata", side_effect=RuntimeError("api 502")):
            keep_going = asr._process_candidate(bid, self.db_path)
        self.assertTrue(keep_going)
        self.assertTrue(asr._is_in_backoff(bid))

        conn = database.get_conn(self.db_path)
        try:
            picked = asr.fetch_next_missing_target_book(conn)
        finally:
            conn.close()
        self.assertIsNone(picked)


if __name__ == "__main__":
    unittest.main()
