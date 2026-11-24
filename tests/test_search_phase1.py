"""Search v2 phase 1: semantic query dedupe, traditional queries, and whole-word vs half-word scoring."""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
EVAL_DIR = os.path.join(os.path.dirname(__file__), "search_eval")

import eval_search  # noqa: E402
from build_bge_index import strip_toc_ads  # noqa: E402
from search import hanzi, term_points  # noqa: E402
from search.finalize import rerank_document  # noqa: E402
from search.hanzi import to_simplified, with_traditional_variants  # noqa: E402
from search.intent import build_semantic_query  # noqa: E402
from search.scoring import pre_score_candidates  # noqa: E402


class SemanticQueryTests(unittest.TestCase):
    def test_concept_already_in_the_sentence_is_not_repeated(self):
        self.assertEqual(build_semantic_query("科幻史诗", ["科幻史诗"]), "科幻史诗")

    def test_new_concepts_are_appended_once(self):
        self.assertEqual(build_semantic_query("讲帝国兴衰的小说", ["帝国", "历史小说", "历史小说"]), "讲帝国兴衰的小说 历史小说")

    def test_at_most_three_concepts_are_appended(self):
        self.assertEqual(build_semantic_query("书", ["甲乙", "丙丁", "戊己", "庚辛"]), "书 甲乙 丙丁 戊己")


class TraditionalVariantTests(unittest.TestCase):
    @staticmethod
    def intent(**fields):
        base = {"primary_terms": [], "synonym_terms": [], "negative_terms": [], "explicit_title": ""}
        return {**base, **fields}

    def test_original_spellings_go_first_in_the_synonyms(self):
        intent = self.intent(primary_terms=["商业策略", "创新"], synonym_terms=["商业", "战略"])
        with_traditional_variants(intent, "關於商業策略與創新的書籍", "关于商业策略与创新的书籍")
        self.assertEqual(intent["synonym_terms"], ["商業策略", "創新", "商业", "战略"])

    def test_length_change_adds_nothing(self):
        intent = self.intent(primary_terms=["创新"])
        with_traditional_variants(intent, "創新!", "创新")
        self.assertEqual(intent["synonym_terms"], [])

    def test_lowercased_mixed_terms_are_found(self):
        intent = self.intent(primary_terms=["ai时代"])
        with_traditional_variants(intent, "AI時代", "AI时代")
        self.assertEqual(intent["synonym_terms"], ["AI時代"])

    def test_title_and_exclusions_keep_their_original_spelling(self):
        intent = self.intent(explicit_title="刻意进化", negative_terms=["小说"])
        with_traditional_variants(intent, "刻意進化 不要小說", "刻意进化 不要小说")
        self.assertEqual(intent["explicit_title_variant"], "刻意進化")
        self.assertEqual(intent["negative_terms"], ["小说", "小說"])

    def test_embedded_nul_does_not_cut_the_text(self):
        self.assertEqual(to_simplified("台灣\x00測試"), "台湾\x00测试")
        self.assertEqual(hanzi._transform_icu("台灣\x00測試"), "台灣\x00測試")

    def test_lone_surrogate_degrades_safely(self):
        self.assertEqual(to_simplified("\ud800"), "\ud800")
        self.assertEqual(to_simplified("台灣\ud800測試"), "台湾\ud800测试")

    def test_expanded_terms_stays_synced_with_synonym_terms(self):
        intent = self.intent(primary_terms=["商业策略"], synonym_terms=["商业"], expanded_terms=["商业"])
        with_traditional_variants(intent, "關於商業策略", "关于商业策略")
        self.assertEqual(intent["synonym_terms"], ["商業策略", "商业"])
        self.assertEqual(intent["expanded_terms"], intent["synonym_terms"])

    def test_standard_simplified_characters_are_kept(self):
        self.assertEqual(to_simplified("那本讲秘密俱乐部的书，著作，大阪"), "那本讲秘密俱乐部的书，著作，大阪")

    def test_traditional_text_is_simplified(self):
        self.assertEqual(to_simplified("關於商業策略與創新的書籍"), "关于商业策略与创新的书籍")


class HalfWordInvariantTests(unittest.TestCase):
    def test_any_half_word_scores_below_the_weakest_whole_word_hit(self):
        weakest_whole_word = min(pts for _, pts in term_points.STACKABLE_FIELD_PTS)
        self.assertLess(term_points.HALF_WORD_TITLE_PTS, weakest_whole_word)
        self.assertLess(term_points.HALF_WORD_TEXT_PTS, weakest_whole_word)

    def test_lower_fields_add_a_capped_share(self):
        hits = {"meta": False, "summary": True, "ai_keywords": True, "toc": True}
        self.assertAlmostEqual(term_points.secondary_field_bonus("summary", 36.0, hits), 0.30 * 36.0)  # capped below 0.25 * 52
        self.assertAlmostEqual(term_points.secondary_field_bonus("toc", 22.0, hits), 0.0)
        self.assertAlmostEqual(term_points.secondary_field_bonus("title", 68.0, {"meta": True, "summary": True}), 0.25 * 80.0)

    def test_title_hit_on_second_half_beats_blurb_hit_on_first(self):
        pts, half, in_title = term_points.half_word_hit("科幻史诗", "银河史诗", "一部科幻小说")
        self.assertEqual((pts, half, in_title), (term_points.BOTH_HALVES_TITLE_PTS, "史诗", True))

    def test_english_words_have_no_halves(self):
        self.assertEqual(term_points.half_word_hit("rust", "trust me", "ruby"), (0.0, "", False))

    def test_generic_half_is_not_evidence_for_both_halves(self):
        self.assertEqual(term_points.meaningful_halves("投资方法"), ["投资"])
        self.assertEqual(term_points.half_word_hit("投资方法", "投资的方法", "")[0], term_points.HALF_WORD_TITLE_PTS)


class RerankDocumentTests(unittest.TestCase):
    def test_missing_fields_do_not_raise(self):
        item = {"title": "书", "subtitle": None, "author": None, "category": None, "_ai_meta": {"keywords": ["甲", 3]}}
        self.assertIn("关键词：甲", rerank_document(item))

    def test_ai_category_overrides_category_and_core_points_filter_before_slice(self):
        item = {
            "title": "书",
            "subtitle": "",
            "author": "作者",
            "category": "旧分类",
            "core_points": [42, "看点一", None, "看点二", "看点三"],
            "summary": "",
            "_ai_meta": {"ai_category": "人工智能与技术", "subfield": "机器学习", "keywords": []},
        }
        doc = rerank_document(item)
        self.assertIn("领域：人工智能与技术 / 机器学习", doc)
        self.assertNotIn("旧分类", doc)
        self.assertIn("看点：看点一；看点二", doc)
        self.assertNotIn("看点三", doc)


class TraditionalPinTests(unittest.TestCase):
    @staticmethod
    def _row(bid, title, author="未知作者"):
        return {
            "id": bid,
            "title": title,
            "subtitle": "",
            "author": author,
            "pinyin_search": "",
            "category": "文学小说与艺术",
            "tags": "[]",
            "summary": "",
            "toc_text": "",
            "isbn": "",
            "core_points": "[]",
            "hyde_queries": "[]",
            "classic_score": 20.0,
            "is_anthology": 0,
        }

    @staticmethod
    def _intent(**fields):
        base = {
            "primary_terms": [],
            "synonym_terms": [],
            "term_weights": {},
            "negative_terms": [],
            "author_terms": [],
            "is_disjunctive": False,
            "is_pinyin": False,
            "wants_classic": False,
            "wants_anthology": False,
            "style_mode": "neutral",
            "parent_nouns": [],
            "tier1_terms": [],
            "tier2_terms": [],
            "explicit_title": "",
        }
        return {**base, **fields}

    def test_long_unquoted_traditional_title_gets_exact_pin(self):
        query = "一個關於認知偏誤與決策心理的深度探討全書"
        row = self._row(1, "一个关于认知偏误与决策心理的深度探讨全书")
        res = pre_score_candidates(query, self._intent(), [1], {1: row}, {}, {}, "", [], False)
        self.assertGreaterEqual(res[0]["_lexical_score"], 480.0)

    def test_traditional_author_query_gets_exact_pin(self):
        row = self._row(1, "超新星纪元", author="刘慈欣")
        intent = self._intent(primary_terms=["刘慈欣"], author_terms=["刘慈欣"], explicit_title="刘慈欣")
        res = pre_score_candidates("劉慈欣", intent, [1], {1: row}, {}, {}, "", [], False)
        self.assertGreaterEqual(res[0]["_lexical_score"], 480.0)


class HoldoutRunnerTests(unittest.TestCase):
    def test_run_holdout_applies_only_and_resets_ai_rerank(self):
        fake_queries = [{"id": "hold-a", "query": "alpha"}, {"id": "hold-b", "query": "beta"}]
        seen = []

        def fake_eval(modes, queries):
            seen.append((modes, [q["id"] for q in queries], eval_search.AI_RERANK))
            return {}

        prev = eval_search.AI_RERANK
        try:
            with mock.patch.object(eval_search, "load_queries", return_value=fake_queries), \
                    mock.patch.object(eval_search, "evaluate_queries", side_effect=fake_eval):
                eval_search.run_holdout(("shelf",), only="hold-b", ai=True)
                eval_search.run_holdout(("shelf",))
        finally:
            eval_search.AI_RERANK = prev

        self.assertEqual(seen, [(("shelf",), ["hold-b"], True), (("shelf",), ["hold-a", "hold-b"], False)])


class TocAdTests(unittest.TestCase):
    def test_promo_entries_go_and_wechat_chapters_stay(self):
        toc = "1.每日发布新书可下载。公众号首页回复书名 | 第三节 案例9：微信——社交席卷一切 | 4.也可以加微信【123】免费领取"
        self.assertEqual(strip_toc_ads(toc), "第三节 案例9：微信——社交席卷一切")


class HoldoutSetTests(unittest.TestCase):
    def test_holdout_cases_stay_out_of_the_tuned_sets(self):
        def ids(name):
            with open(os.path.join(EVAL_DIR, name), encoding="utf-8") as f:
                return [q["id"] for q in json.load(f)["queries"]]

        holdout = ids("holdout_queries.json")
        self.assertGreaterEqual(len(holdout), 12)
        self.assertEqual(len(holdout), len(set(holdout)))
        self.assertFalse(set(holdout) & set(ids("queries.json") + ids("generated_queries.json")))


class QueryLengthCapTest(unittest.TestCase):
    def test_pasted_page_is_searched_by_its_first_chars_only(self):
        from search import retrieval

        seen = []
        empty = {"items": [], "total": 0, "elapsed_ms": 0.0}
        with mock.patch.object(retrieval, "_retrieve_one_language", lambda q, **_: seen.append(q) or dict(empty)), \
             mock.patch.object(retrieval, "_translate_english_query", lambda q: ""):
            retrieval._execute_unified_hybrid_retrieval("词" * 5000)
        self.assertEqual(seen, ["词" * retrieval.MAX_QUERY_CHARS])


if __name__ == "__main__":
    unittest.main()
