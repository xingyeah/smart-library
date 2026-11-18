"""Engine-chain tests for backend/translator.py with all network engines and the cache stubbed out."""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import translator as tr  # noqa: E402


class EngineChainTest(unittest.TestCase):
    def setUp(self):
        patches = [
            mock.patch.object(tr, "get_cached_translation", return_value=None),
            mock.patch.object(tr, "save_cached_translation"),
            mock.patch.object(tr, "_claude", return_value=object()),
            mock.patch.object(tr, "_deepseek_key", return_value=None),
            mock.patch.object(tr, "_translate_via_mymemory", return_value=None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_lookup_routing(self):
        self.assertTrue(tr._is_lookup("serendipity"))
        self.assertTrue(tr._is_lookup("take for granted"))
        self.assertFalse(tr._is_lookup("You're late, Mara said."))

    def test_batch_falls_through_claude_apple_web(self):
        paras = ["First paragraph here, long enough.", "", "Second one.", "Third one."]
        google = lambda text, *a: {"translation": f"G:{text}", "engine": "google"}  # noqa: E731
        with mock.patch.object(tr, "_translate_batch_via_claude", return_value={0: "C0"}) as claude, \
                mock.patch.object(tr, "_translate_batch_via_apple", return_value=[]) as apple, \
                mock.patch.object(tr, "_translate_via_google", side_effect=google):
            out = tr.batch_translate_paragraphs(paras, context={"title": "T"})
        self.assertEqual(claude.call_count, 1)  # one call for the page, no per-paragraph retry
        self.assertEqual(apple.call_count, 1)
        self.assertEqual([r["engine"] for r in out], ["claude", "", "google", "google"])
        self.assertEqual(out[0]["translation"], "C0")
        self.assertEqual(out[3]["translation"], "G:Third one.")

    def test_dead_web_engine_is_dropped_after_first_failure(self):
        paras = ["One sentence here.", "Two sentence here.", "Three sentence here."]
        with mock.patch.object(tr, "_claude", return_value=None), \
                mock.patch.object(tr, "_translate_batch_via_apple", return_value=[]), \
                mock.patch.object(tr, "_translate_via_google", return_value=None) as google, \
                mock.patch.object(tr, "_translate_via_mymemory",
                                  side_effect=lambda text, *a: {"translation": f"M:{text}", "engine": "mymemory"}):
            out = tr.batch_translate_paragraphs(paras)
        self.assertEqual(google.call_count, 1)
        self.assertEqual([r["engine"] for r in out], ["mymemory"] * 3)

    def test_word_lookup_skips_llm(self):
        with mock.patch.object(tr, "_translate_batch_via_claude") as claude, \
                mock.patch.object(tr, "_translate_via_google", return_value={"ok": True, "translation": "意外发现", "engine": "google"}):
            res = tr.translate_text("serendipity")
        claude.assert_not_called()
        self.assertEqual(res["engine"], "google")

    def test_google_cached_passage_upgraded_when_claude_available(self):
        cached = {"ok": True, "translation": "old", "engine": "google"}
        with mock.patch.object(tr, "get_cached_translation", return_value=cached):
            self.assertIsNone(tr._usable_cache("A full sentence to translate.", "auto", "zh-CN"))
            self.assertEqual(tr._usable_cache("word", "auto", "zh-CN"), cached)


class ManualEngineTest(unittest.TestCase):
    def test_engine_order(self):
        self.assertEqual(tr._engine_order("auto", lookup=False), ["claude", "deepseek", "apple", "google", "mymemory"])
        self.assertEqual(tr._engine_order("deepseek", lookup=False), ["deepseek", "claude", "apple", "google", "mymemory"])
        self.assertEqual(tr._engine_order("claude", lookup=True), ["claude", "google", "mymemory"])
        self.assertEqual(tr._engine_order("<script>", lookup=False), tr._engine_order("auto", lookup=False))

    def test_cache_only_from_chosen_engine(self):
        cached = {"ok": True, "translation": "old", "engine": "google"}
        with mock.patch.object(tr, "get_cached_translation", return_value=cached):
            self.assertEqual(tr._usable_cache("A full sentence here.", "auto", "zh-CN", "google"), cached)
            self.assertIsNone(tr._usable_cache("A full sentence here.", "auto", "zh-CN", "apple"))

    def test_chosen_engine_falls_back_and_reports(self):
        with mock.patch.object(tr, "get_cached_translation", return_value=None), \
                mock.patch.object(tr, "save_cached_translation"), \
                mock.patch.object(tr, "_claude", return_value=object()), \
                mock.patch.object(tr, "_deepseek_key", return_value=None), \
                mock.patch.object(tr, "_translate_batch_via_apple", return_value=[]), \
                mock.patch.object(tr, "_translate_batch_via_claude", return_value={0: "C"}) as claude, \
                mock.patch.object(tr, "_translate_via_google", return_value=None):
            res = tr.translate_text("Apple was chosen but is missing.", engine="apple")
        self.assertEqual(res["engine"], "claude")
        claude.assert_called_once()


class DeepSeekTest(unittest.TestCase):
    def _fake_response(self, body):
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = json.dumps(body).encode("utf-8")
        return resp

    def test_parses_tagged_reply_and_sends_context(self):
        body = {"choices": [{"message": {"content": '<p id="0">耐心</p>\n<p id="1">速度</p>'}}]}
        with mock.patch.object(tr, "_deepseek_key", return_value="k"), \
                mock.patch.dict(tr.os.environ, {"DEEPSEEK_API_KEY": "k"}), \
                mock.patch.object(tr.urllib.request, "urlopen", return_value=self._fake_response(body)) as urlopen:
            out = tr._translate_batch_via_deepseek(["Patience.", "Speed."], "zh-CN", {"title": "Book T"})
        self.assertEqual(out, {0: "耐心", 1: "速度"})
        req = urlopen.call_args[0][0]
        self.assertTrue(req.get_header("Authorization") == "Bearer k", "fake key not used")  # never echo the header
        self.assertIn("书名：Book T", json.loads(req.data)["messages"][1]["content"])

    def test_api_error_pauses_engine(self):
        with mock.patch.dict(tr.os.environ, {"DEEPSEEK_API_KEY": "k"}), \
                mock.patch.object(tr.urllib.request, "urlopen", side_effect=OSError("402 Payment Required")), \
                mock.patch.object(tr, "_deepseek_paused_until", 0.0):
            self.assertEqual(tr._translate_batch_via_deepseek(["Hi there."]), {})
            self.assertIsNone(tr._deepseek_key())


if __name__ == "__main__":
    unittest.main()
