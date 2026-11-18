"""
Search quality gate: runs the labeled query set (tests/search_eval/queries.json) through the real
pipeline and fails if quality drops below tests/search_eval/baseline.json.

Shelf search runs by default (~5s). The describe path uses the cross-encoder (~50s):
  SEARCH_EVAL_DESCRIBE=1 .venv/bin/python -m unittest tests.test_search_quality -v
Refresh the baseline after an intended ranking change:
  .venv/bin/python backend/eval_search.py --save tests/search_eval/baseline.json
"""

import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from config import DB_PATH  # noqa: E402

BASELINE_PATH = ROOT / "tests" / "search_eval" / "baseline.json"
NDCG_TOLERANCE = 0.02
HIT3_TOLERANCE = 0.05


@unittest.skipUnless(DB_PATH.exists() and BASELINE_PATH.exists(), "library.db or baseline.json not found")
class SearchQualityGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import eval_search

        modes = ["shelf"] + (["describe"] if os.environ.get("SEARCH_EVAL_DESCRIBE") == "1" else [])
        cls.report = eval_search.run(modes)
        cls.baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))

    def check_mode(self, mode):
        if mode not in self.report:
            self.skipTest(f"{mode} not run (set SEARCH_EVAL_DESCRIBE=1)")
        # Gate on hand-labeled + human-verified queries only; DeepSeek-only labels are reference
        res, base = self.report[mode]["gate"], self.baseline[mode].get("gate", self.baseline[mode])
        rows = self.report[mode]["queries"]
        regressed = [
            f"{r['id']}: {base_q['ndcg']:.3f} -> {r['ndcg']:.3f}"
            for r in rows
            for base_q in self.baseline[mode]["queries"]
            if base_q["id"] == r["id"] and r["ndcg"] < base_q["ndcg"] - 0.15
        ]
        msg = f"{mode}: nDCG {res['ndcg']:.4f} (baseline {base['ndcg']:.4f}); big per-query drops: {regressed}"
        self.assertGreaterEqual(res["ndcg"], base["ndcg"] - NDCG_TOLERANCE, msg)
        self.assertGreaterEqual(res["hit3"], base["hit3"] - HIT3_TOLERANCE, msg)
        self.assertLessEqual(res["bad5"], base["bad5"], f"{mode}: known-wrong books reached the top 5")

    def test_shelf_quality(self):
        self.check_mode("shelf")

    def test_describe_quality(self):
        self.check_mode("describe")



@unittest.skipUnless(DB_PATH.exists(), "library.db not found")
class InterestRecommendationTest(unittest.TestCase):
    def test_never_recommends_marked_books_or_their_copies(self):
        import sqlite3

        from search_engine import recommend_by_interests

        marked_sql = "is_favorite = 1 OR reading_status != 'unread' OR user_rating > 0"
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            marked = {r[0] for r in conn.execute(
                f"SELECT id FROM books WHERE {marked_sql} OR norm_title IN (SELECT norm_title FROM books WHERE {marked_sql})"
            )}
        finally:
            conn.close()
        res = recommend_by_interests(top_k=10)
        ids = [b["id"] for b in res["items"]]
        self.assertEqual(len(ids), 10)
        self.assertFalse(set(ids) & marked)


AI_BASELINE_PATH = ROOT / "tests" / "search_eval" / "baseline_ai.json"


@unittest.skipUnless(
    os.environ.get("SEARCH_EVAL_AI") == "1" and AI_BASELINE_PATH.exists(),
    "AI rerank gate costs ~CNY 2 per uncached run: set SEARCH_EVAL_AI=1",
)
class AiRerankQualityGate(unittest.TestCase):
    """AI mode must hold its own baseline and stay ahead of the standard baseline."""

    def test_ai_mode(self):
        import eval_search

        report = eval_search.run(["shelf", "describe"], ai=True)
        ai_base = json.loads(AI_BASELINE_PATH.read_text(encoding="utf-8"))
        std_base = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        for mode in ("shelf", "describe"):
            got = report[mode]["gate"]
            self.assertGreaterEqual(got["ndcg"], ai_base[mode]["gate"]["ndcg"] - NDCG_TOLERANCE, mode)
            self.assertGreaterEqual(got["ndcg"], std_base[mode]["gate"]["ndcg"], f"{mode}: AI mode fell behind standard")
            self.assertLessEqual(got["bad5"], ai_base[mode]["gate"]["bad5"], mode)


if __name__ == "__main__":
    unittest.main()
