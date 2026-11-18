"""Unit checks for search helpers that need no library database."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import search.retrieval as retrieval
import search_engine as se  # noqa: E402


def _book(book_id, title, score, author="a", volume=""):
    return {"id": book_id, "title": title, "norm_author": author, "volume_part": volume, "raw_rank_score": score}


class FinalizeRankedPoolTest(unittest.TestCase):
    def test_one_slot_per_work_but_series_volumes_and_authors_stay(self):
        pool = [
            _book(1, "区块链革命", 90), _book(2, "区块链革命", 80),               # same work twice
            _book(3, "牛津通识读本 (Stoicism)", 70), _book(4, "牛津通识读本 (Fascism)", 69),  # same series, different books
            _book(5, "数学", 68, author="b"), _book(6, "数学", 67, author="c"),   # same title, different authors
            _book(7, "三体", 66, volume="1"), _book(8, "三体", 65, volume="2"),
            _book(9, "噪声", 10),                                               # below 28% of the top score
        ]
        out = se._finalize_ranked_pool(pool, max_results=50)
        self.assertEqual([b["id"] for b in out], [1, 3, 4, 5, 6, 7, 8])
        self.assertEqual([b["rank"] for b in out], list(range(1, 8)))


class EnglishQueryTest(unittest.TestCase):
    def test_plural_and_whole_word_matching(self):
        self.assertTrue(se._has_term("cryptocurrency", "the future of cryptocurrencies"))
        self.assertTrue(se._has_term("startup", "lean startups at scale"))
        self.assertFalse(se._has_term("history", "prehistory of art"))
        self.assertTrue(se._has_term("比特币", "精通比特币"))

    def test_english_sentence_is_not_pinyin_and_drops_filler(self):
        intent = se.analyze_query_intent("books about existentialism for beginners")
        self.assertEqual(intent["primary_terms"], ["existentialism"])
        self.assertEqual(intent["style_mode"], "intro_popsci")
        self.assertEqual(se._detect_pinyin_query("sikao kuai yu man")[0], True)

    def test_only_english_sentences_are_translated_and_merged(self):
        with mock.patch("translator._translate_batch_via_apple", return_value=["存在主义入门"]) as apple:
            se._QUERY_TRANSLATION_CACHE.clear()
            self.assertEqual(se._translate_english_query("存在主义 入门 书"), "")
            self.assertEqual(se._translate_english_query("python"), "")
            self.assertEqual(se._translate_english_query("books about existentialism"), "存在主义入门")
            self.assertEqual(se._translate_english_query("books about existentialism"), "存在主义入门")
        apple.assert_called_once()  # second call served from the cache
        runs = {"books about existentialism": [_book(1, "Existentialism", 100)], "存在主义入门": [_book(2, "存在主义", 90), _book(1, "Existentialism", 40)]}

        def fake(query, **_):
            return {"items": [dict(b) for b in runs[query]], "total": 0, "elapsed_ms": 1.0, "inferred_categories": []}

        with mock.patch.object(retrieval, "_retrieve_one_language", side_effect=fake), \
                mock.patch.object(retrieval, "_translate_english_query", return_value="存在主义入门"):
            res = se._execute_unified_hybrid_retrieval("books about existentialism", max_results=10)
        self.assertEqual([(b["id"], b["raw_rank_score"]) for b in res["items"]], [(1, 100), (2, 90)])
        self.assertEqual(res["translated_query"], "存在主义入门")


if __name__ == "__main__":
    unittest.main()
