"""AI rerank + daily cap, with DeepSeek mocked and an in-memory SQLite (no network, library DB untouched)."""

import json
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import ai_search  # noqa: E402
import deepseek_client as ds  # noqa: E402
import migrations  # noqa: E402


class _Conn:
    """One shared in-memory DB whose close() is a no-op, so cache and usage rows survive between calls."""

    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        migrations._migrate_v9(self.db)

    def __getattr__(self, name):
        return getattr(self.db, name)

    def close(self):
        pass


def _books(*ids):
    return [{"id": i, "title": f"book {i}", "author": "a", "summary": "s"} for i in ids]


class RerankTest(unittest.TestCase):
    def setUp(self):
        conn = _Conn()
        for p in (
            mock.patch.object(ai_search, "get_conn", return_value=conn),
            mock.patch.object(ds, "get_conn", return_value=conn),
            mock.patch.object(ds, "api_key", return_value="k"),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_reorders_by_score_and_drops_invalid_scores(self):
        reply = {"scores": {"1": 9, "2": 3, "999": 10, "3": "x", "4": 11}}
        with mock.patch.object(ds, "chat_json", return_value=(reply, {})) as chat:
            items, reason = ai_search.rerank("q", _books(2, 1, 3, 4))
        self.assertEqual(reason, "")
        self.assertEqual([b["id"] for b in items], [1, 2, 3, 4])  # unscored keep their order after scored
        self.assertEqual(items[0]["ai_score"], 9)
        self.assertIsNone(items[2]["ai_score"])
        self.assertEqual(chat.call_args.kwargs["feature"], "search")
        # Same query + candidates: served from cache, no second call
        with mock.patch.object(ds, "chat_json") as chat2:
            ai_search.rerank("q", _books(2, 1, 3, 4))
        chat2.assert_not_called()

    def test_failures_keep_standard_order_with_reason(self):
        for exc, reason in ((ds.DailyCapReached("cap"), "cap"), (OSError("timeout"), "error")):
            with mock.patch.object(ds, "chat_json", side_effect=exc):
                items, got = ai_search.rerank(f"q-{reason}", _books(5, 6))
            self.assertEqual(([b["id"] for b in items], got), ([5, 6], reason))
        with mock.patch.object(ds, "api_key", return_value=None), mock.patch.object(ds, "chat_json") as chat:
            self.assertEqual(ai_search.rerank("q", _books(5, 6))[1], "nokey")
        chat.assert_not_called()

    def test_pick_books_validates_caches_and_reports_failures(self):
        reply = {"picks": [{"id": 3, "reason": "  好书 " + "长" * 100}, {"id": 999, "reason": "x"}, {"id": 3, "reason": "dup"},
                           {"id": "bad"}, {"id": 1}, {"id": 2, "reason": "r2"}]}
        with mock.patch.object(ds, "chat_json", return_value=(reply, {})) as chat:
            picks, status = ai_search.pick_books("画像", _books(1, 2, 3), top_k=2)
        self.assertEqual(status, "")
        self.assertEqual([i for i, _ in picks], [3, 1])  # unknown, repeated and malformed ids dropped, capped at top_k
        self.assertTrue(picks[0][1].startswith("好书") and len(picks[0][1]) == 80)
        self.assertEqual(chat.call_args.kwargs["feature"], "recommend")
        with mock.patch.object(ds, "chat_json") as chat2:
            self.assertEqual(ai_search.pick_books("画像", _books(1, 2, 3), top_k=2), (picks, ""))
        chat2.assert_not_called()
        with mock.patch.object(ds, "chat_json", side_effect=ds.DailyCapReached("cap")):
            self.assertEqual(ai_search.pick_books("别的画像", _books(1, 2)), ([], "cap"))
        with mock.patch.object(ds, "chat_json", return_value=({"picks": [{"id": 999}]}, {})):
            self.assertEqual(ai_search.pick_books("第三个画像", _books(1, 2)), ([], "error"))


class DailyCapTest(unittest.TestCase):
    def setUp(self):
        conn = _Conn()
        for p in (mock.patch.object(ds, "get_conn", return_value=conn), mock.patch.object(ds, "api_key", return_value="k")):
            p.start()
            self.addCleanup(p.stop)

    def _ok_response(self):
        resp = mock.MagicMock()
        body = {"choices": [{"message": {"content": "{}"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        resp.__enter__.return_value.read.return_value = json.dumps(body).encode("utf-8")
        return resp

    def test_cap_blocks_search_and_translate_only(self):
        ds.record_usage("search", ds.FLASH_MODEL, {"prompt_tokens": 3_000_000, "completion_tokens": 0})  # CNY 6 > 5
        with mock.patch.object(ds.urllib.request, "urlopen") as urlopen:
            for feature in ("search", "translate"):
                with self.assertRaises(ds.DailyCapReached):
                    ds.chat([{"role": "user", "content": "x"}], feature=feature)
            urlopen.assert_not_called()
        with mock.patch.object(ds.urllib.request, "urlopen", return_value=self._ok_response()):
            ds.chat([{"role": "user", "content": "x"}], feature="enrich")  # offline work is logged, not capped
        summary = ds.usage_summary()
        self.assertEqual(summary["today_capped"], 6.0)
        self.assertIn("enrich", summary["today"])

    def test_claude_translation_counts_toward_cap(self):
        import translator

        resp = mock.MagicMock(stop_reason="end_turn", content=[mock.MagicMock(type="text", text='<p id="0">你好</p>')])
        resp.usage.input_tokens, resp.usage.output_tokens = 1_000_000, 0
        client = mock.MagicMock()
        client.messages.create.return_value = resp
        with mock.patch.object(translator, "_claude", return_value=client):
            self.assertEqual(translator._translate_batch_via_claude(["hello"]), {0: "你好"})
            self.assertAlmostEqual(ds.usage_summary()["today"]["translate"], 0.72, places=3)
            ds.record_usage("search", ds.FLASH_MODEL, {"prompt_tokens": 3_000_000, "completion_tokens": 0})
            client.messages.create.reset_mock()
            self.assertEqual(translator._translate_batch_via_claude(["hello"]), {})
            client.messages.create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
