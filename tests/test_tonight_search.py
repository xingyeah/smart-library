"""Tonight search: DeepSeek reads the sentence and judges books; a repeated sentence serves what was not answered."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import database  # noqa: E402
import deepseek_client as ds  # noqa: E402
import skips  # noqa: E402
import tonight_ai  # noqa: E402
import tonight_search  # noqa: E402

QUERY = "宋朝 历史"
UNDERSTANDING = {"search_lines": ["宋朝历史", "Song dynasty"], "keywords": ["宋朝"], "categories": ["历史与地缘政治"], "avoid": ""}


class TonightSearchTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "tonight_search.db"
        database.init_db(self.db_path)
        self.pool_sizes = []
        self.found_ids = list(range(1, 31))
        conn_factory = lambda: database.get_conn(self.db_path)  # noqa: E731
        tx_factory = lambda: database.write_tx(self.db_path)  # noqa: E731
        patches = {
            "tonight_search.write_tx": tx_factory, "tonight_search.get_conn": conn_factory,
            "tonight_ai.write_tx": tx_factory, "tonight_ai.get_conn": conn_factory,
            "skips.write_tx": tx_factory,
            "tonight_search._execute_unified_hybrid_retrieval": self.fake_retrieval,
        }
        for target, replacement in patches.items():
            patcher = mock.patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        for book_id in self.found_ids:
            self.add_book(book_id)

    def fake_retrieval(self, raw_query, filters, max_results, enable_cross_encoder):
        self.pool_sizes.append(max_results)
        return {"items": [{"id": b, "norm_author": f"author{b}", "title": f"书{b}"} for b in self.found_ids[:max_results]]}

    def add_book(self, book_id, status="unread", author=None):
        conn = database.get_conn(self.db_path)
        conn.execute(
            "INSERT INTO books (id, dedup_key, title, norm_title, author, norm_author, primary_format, primary_path, "
            "reading_status, category) VALUES (?, ?, ?, ?, ?, ?, 'epub', ?, ?, '历史与地缘政治')",
            (book_id, f"k{book_id}", f"书{book_id}", f"书{book_id}", author or f"a{book_id}", author or f"a{book_id}",
             f"/tmp/{book_id}", status),
        )
        conn.commit()
        conn.close()

    def set_status(self, book_id, status):
        conn = database.get_conn(self.db_path)
        conn.execute("UPDATE books SET reading_status = ? WHERE id = ?", (status, book_id))
        conn.commit()
        conn.close()

    def batch_ids(self, query=QUERY):
        return [item["id"] for item in tonight_search.tonight_search(query)["items"]]


class PlainFallbackTests(TonightSearchTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(ds, "api_key", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_without_deepseek_the_plain_ranking_is_served_and_says_why(self):
        result = tonight_search.tonight_search(QUERY)
        self.assertEqual([item["id"] for item in result["items"]], list(range(1, 11)))
        self.assertEqual(result["ai_status"], "nokey")

    def test_leaves_out_answered_and_finished_books_but_not_wanted_elsewhere(self):
        skips.record_skip(1, skips.SKIP_CONTEXT_SEARCH, "宋朝   历史 ")
        skips.record_skip(2, skips.SKIP_CONTEXT_SEARCH, "别的句子")
        tonight_search.record_search_want(3, QUERY)
        self.set_status(4, "read")
        self.set_status(5, "want")
        batch = self.batch_ids()
        for gone in (1, 3, 4):
            self.assertNotIn(gone, batch)
        self.assertIn(2, batch)
        self.assertIn(5, batch)

    def test_reset_brings_skipped_books_back_but_not_wanted_ones(self):
        skips.record_skip(1, skips.SKIP_CONTEXT_SEARCH, QUERY)
        tonight_search.record_search_want(2, QUERY)
        tonight_search.reset_search(QUERY)
        batch = self.batch_ids()
        self.assertIn(1, batch)
        self.assertNotIn(2, batch)

    def test_widens_the_pool_when_few_unseen_books_are_left(self):
        self.found_ids = list(range(1, 81))
        for book_id in range(31, 81):
            self.add_book(book_id)
        for book_id in range(1, 56):
            skips.record_skip(book_id, skips.SKIP_CONTEXT_SEARCH, QUERY)
        self.assertEqual(len(self.batch_ids()), tonight_search.BATCH_SIZE)
        self.assertEqual(self.pool_sizes, [tonight_search.FIRST_POOL, tonight_search.WIDE_POOL])

    def test_undo_want_lets_the_book_back(self):
        tonight_search.record_search_want(1, QUERY)
        self.assertTrue(tonight_search.undo_search_want(1))
        self.assertFalse(tonight_search.undo_search_want(1))
        self.assertIn(1, self.batch_ids())

    def test_want_of_a_missing_book_is_refused(self):
        with self.assertRaises(tonight_search.TonightSearchError):
            tonight_search.record_search_want(999, QUERY)


class FakeDeepSeek:
    """Reads the sentence as UNDERSTANDING; scores a book 9 when its id is in `good`, else 2."""

    def __init__(self, good):
        self.good = set(good)
        self.prompts = []

    def __call__(self, messages, **options):
        prompt = messages[1]["content"]
        self.prompts.append(prompt)
        if "读者的话" in prompt:
            return dict(UNDERSTANDING, categories=["历史与地缘政治", "不存在的分类"]), {}
        ids = [int(line[1:line.index("]")]) for line in prompt.splitlines() if line.startswith("[")]
        books = {str(i): {"score": 9 if i in self.good else 2, "reason": f"理由{i}"} for i in ids}
        return {"books": books}, {}


class AiSearchTests(TonightSearchTestCase):
    def use_deepseek(self, good):
        fake = FakeDeepSeek(good)
        for patcher in (mock.patch.object(ds, "api_key", return_value="k"), mock.patch.object(ds, "chat_json", fake)):
            patcher.start()
            self.addCleanup(patcher.stop)
        return fake

    def test_serves_only_books_judged_worth_reading_best_first_with_reasons(self):
        self.use_deepseek(good={3, 7, 12})
        result = tonight_search.tonight_search(QUERY)
        self.assertEqual(result["ai_status"], "")
        self.assertEqual(sorted(item["id"] for item in result["items"]), [3, 7, 12])
        self.assertEqual({item["ai_reason"] for item in result["items"]}, {"理由3", "理由7", "理由12"})

    def test_understanding_keeps_only_known_categories_and_is_asked_once(self):
        fake = self.use_deepseek(good=set(range(1, 31)))
        tonight_search.tonight_search(QUERY)
        tonight_search.tonight_search(QUERY)
        conn = database.get_conn(self.db_path)
        stored = json.loads(conn.execute("SELECT payload FROM ai_query_understanding").fetchone()[0])
        conn.close()
        self.assertEqual(stored["categories"], ["历史与地缘政治"])
        self.assertEqual(sum("读者的话" in p for p in fake.prompts), 1)

    def test_asking_again_serves_kept_books_without_judging_again(self):
        fake = self.use_deepseek(good=set(range(1, 31)))
        first = self.batch_ids()
        for book_id in first:
            skips.record_skip(book_id, skips.SKIP_CONTEXT_SEARCH, QUERY)
        judge_calls = sum("候选书" in p for p in fake.prompts)
        second = self.batch_ids()
        self.assertFalse(set(first) & set(second))
        self.assertEqual(len(second), tonight_search.BATCH_SIZE)
        self.assertEqual(sum("候选书" in p for p in fake.prompts), judge_calls)

    def test_wanted_titles_reach_the_judge_and_pull_same_author_books_up(self):
        fake = self.use_deepseek(good={20, 21, 22, 23, 24})
        conn = database.get_conn(self.db_path)
        conn.execute("UPDATE books SET norm_author = 'same' WHERE id IN (2, 24)")
        conn.execute("UPDATE books SET category = '人物传记与回忆录' WHERE id = 2")
        conn.commit()
        conn.close()
        tonight_search.record_search_want(2, QUERY)
        self.assertEqual(self.batch_ids()[0], 24)
        self.assertIn("想读过：《书2》", [p for p in fake.prompts if "候选书" in p][0])

    def test_exhausted_after_two_rounds_with_nothing_worth_keeping(self):
        self.found_ids = list(range(1, 31))
        self.use_deepseek(good=set())
        result = tonight_search.tonight_search(QUERY)
        self.assertEqual(result["items"], [])
        self.assertTrue(result["exhausted"])

    def test_judge_failure_without_kept_books_falls_back_to_the_plain_ranking(self):
        def failing(messages, **options):
            if "读者的话" in messages[1]["content"]:
                return UNDERSTANDING, {}
            raise ds.DailyCapReached("cap")
        for patcher in (mock.patch.object(ds, "api_key", return_value="k"), mock.patch.object(ds, "chat_json", failing)):
            patcher.start()
            self.addCleanup(patcher.stop)
        result = tonight_search.tonight_search(QUERY)
        self.assertEqual(result["ai_status"], "cap")
        self.assertEqual(len(result["items"]), tonight_search.BATCH_SIZE)

    def test_clean_verdicts_rejects_unknown_ids_and_bad_scores(self):
        reply = {"books": {"1": {"score": 8, "reason": "好"}, "99": {"score": 9}, "2": {"score": 11}, "3": "x"}}
        self.assertEqual(tonight_ai._clean_verdicts(reply, [1, 2, 3]), {1: {"score": 8, "reason": "好"}})


if __name__ == "__main__":
    unittest.main()
