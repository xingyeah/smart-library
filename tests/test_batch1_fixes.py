import inspect
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import config
import deepseek_client
from enrichers.base import BaseBookEnricher
from http_api import gate, responses, state
from http_api.handlers import translate as translate_handler
from search.shelf import _build_pub_period_clause


class _DummyWfile:
    def __init__(self):
        self.buf = bytearray()

    def write(self, data: bytes) -> None:
        self.buf.extend(data)


class _DummyHandler:
    def __init__(self, path: str = "/", headers=None, body: bytes = b"", client_ip: str = "192.168.1.50"):
        self.path = path
        self.headers = headers or {}
        self.rfile = io.BytesIO(body)
        self.wfile = _DummyWfile()
        self.client_address = (client_ip, 12345)
        self.status = None
        self.sent_headers = []

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, k: str, v: str) -> None:
        self.sent_headers.append((k, v))

    def end_headers(self) -> None:
        pass


class Batch1PubPeriodTests(unittest.TestCase):
    def test_pub_period_1900s_and_pre1900_match_expected_years(self):
        c1900 = _build_pub_period_clause("1900s")
        cpre = _build_pub_period_clause("pre1900")
        self.assertTrue(c1900)
        self.assertTrue(cpre)
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE books (id INT, pub_year TEXT)")
        conn.executemany(
            "INSERT INTO books VALUES (?, ?)",
            [(1, "1850"), (2, "1955"), (3, "2023"), (4, "")],
        )
        rows_1900 = [r[0] for r in conn.execute(f"SELECT id FROM books b WHERE {c1900}")]
        rows_pre = [r[0] for r in conn.execute(f"SELECT id FROM books b WHERE {cpre}")]
        self.assertEqual(rows_1900, [2])
        self.assertEqual(rows_pre, [1])
        conn.close()


class Batch1SecurityTests(unittest.TestCase):
    def test_is_path_within_rejects_sibling_prefix_and_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            lib_dir = root / "Library"
            sibling = root / "Library_evil"
            lib_dir.mkdir()
            sibling.mkdir()
            inside = lib_dir / "book.epub"
            inside.touch()
            outside = sibling / "book.epub"
            outside.touch()

            self.assertTrue(config.is_path_within(inside, lib_dir))
            self.assertFalse(config.is_path_within(lib_dir, lib_dir))
            self.assertFalse(config.is_path_within(outside, lib_dir))
            self.assertFalse(config.is_path_within(lib_dir / ".." / "Library_evil" / "book.epub", lib_dir))
            self.assertFalse(config.is_path_within("", lib_dir))

    def test_read_json_body_enforces_size_and_object_type(self):
        empty_h = _DummyHandler(headers={"Content-Length": "0"})
        self.assertEqual(responses.read_json_body(empty_h), {})

        big_h = _DummyHandler(headers={"Content-Length": str(responses.MAX_JSON_BODY_BYTES + 1)})
        with self.assertRaises(responses.PayloadTooLargeError):
            responses.read_json_body(big_h)

        for bad_hdr in ("abc", "-5"):
            with self.assertRaises(responses.InvalidJsonBodyError):
                responses.read_json_body(_DummyHandler(headers={"Content-Length": bad_hdr}))

        bad_json = b"{not json"
        with self.assertRaises(responses.InvalidJsonBodyError):
            responses.read_json_body(_DummyHandler(headers={"Content-Length": str(len(bad_json))}, body=bad_json))

        arr_json = b"[1, 2, 3]"
        with self.assertRaises(responses.InvalidJsonBodyError):
            responses.read_json_body(_DummyHandler(headers={"Content-Length": str(len(arr_json))}, body=arr_json))

    def test_gate_blocks_protocol_relative_redirect(self):
        orig_tok = state.LAN_TOKEN
        try:
            state.LAN_TOKEN = "secret123"
            h = _DummyHandler(path="//evil.com?token=secret123&next=1")
            allowed = gate.check_lan_gate(h)
            self.assertFalse(allowed)
            self.assertEqual(h.status, 302)
            locs = [v for k, v in h.sent_headers if k == "Location"]
            self.assertEqual(locs, ["/?next=1"])
        finally:
            state.LAN_TOKEN = orig_tok

    def test_translate_handlers_reject_invalid_language_codes(self):
        h1 = _DummyHandler()
        translate_handler.handle_post_translate(h1, {}, {"text": "hi", "source_lang": "en&tl=fr", "target_lang": "zh-CN"})
        self.assertEqual(h1.status, 400)
        self.assertIn("invalid language code", h1.wfile.buf.decode("utf-8"))

        h2 = _DummyHandler()
        translate_handler.handle_post_translate_batch(h2, {}, {"paragraphs": ["hi"], "source_lang": "en", "target_lang": "../bad"})
        self.assertEqual(h2.status, 400)
        self.assertIn("invalid language code", h2.wfile.buf.decode("utf-8"))

    def test_no_unverified_ssl_fallback_remains(self):
        self.assertNotIn("_create_unverified_context", inspect.getsource(deepseek_client))
        self.assertNotIn("_create_unverified_context", inspect.getsource(BaseBookEnricher))


if __name__ == "__main__":
    unittest.main()
