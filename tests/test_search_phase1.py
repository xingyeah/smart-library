"""Search v2 phase 1: semantic query dedupe, traditional queries, and whole-word vs half-word scoring."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from build_bge_index import strip_toc_ads  # noqa: E402
from search import term_points  # noqa: E402
from search.finalize import rerank_document  # noqa: E402
from search.hanzi import to_simplified, with_traditional_variants  # noqa: E402
from search.intent import build_semantic_query  # noqa: E402


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
        self.assertEqual(to_simplified("台灣\x00測試"), "台灣\x00測試")


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


class TocAdTests(unittest.TestCase):
    def test_promo_entries_go_and_wechat_chapters_stay(self):
        toc = "1.每日发布新书可下载。公众号首页回复书名 | 第三节 案例9：微信——社交席卷一切 | 4.也可以加微信【123】免费领取"
        self.assertEqual(strip_toc_ads(toc), "第三节 案例9：微信——社交席卷一切")


if __name__ == "__main__":
    unittest.main()
