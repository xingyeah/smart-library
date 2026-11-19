"""Search v2 phase 1: semantic query dedupe, traditional queries, and whole-word vs half-word scoring."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from search import scoring  # noqa: E402
from search.hanzi import with_traditional_variants  # noqa: E402
from search.intent import build_semantic_query  # noqa: E402


class SemanticQueryTests(unittest.TestCase):
    def test_concept_already_in_the_sentence_is_not_repeated(self):
        self.assertEqual(build_semantic_query("科幻史诗", ["科幻史诗"]), "科幻史诗")

    def test_new_concepts_are_appended_once(self):
        self.assertEqual(build_semantic_query("讲帝国兴衰的小说", ["帝国", "历史小说", "历史小说"]), "讲帝国兴衰的小说 历史小说")


class TraditionalVariantTests(unittest.TestCase):
    def test_original_spelling_of_each_term_becomes_a_synonym(self):
        intent = {"primary_terms": ["商业策略", "创新"], "synonym_terms": []}
        with_traditional_variants(intent, "關於商業策略與創新的書籍", "关于商业策略与创新的书籍")
        self.assertEqual(intent["synonym_terms"], ["商業策略", "創新"])

    def test_length_change_adds_nothing(self):
        intent = {"primary_terms": ["创新"], "synonym_terms": []}
        with_traditional_variants(intent, "創新!", "创新")
        self.assertEqual(intent["synonym_terms"], [])


class HalfWordInvariantTests(unittest.TestCase):
    def test_any_half_word_scores_below_the_weakest_whole_word_hit(self):
        weakest_whole_word = min(pts for _, pts in scoring.STACKABLE_FIELD_PTS)
        self.assertLess(scoring.HALF_WORD_TITLE_PTS, weakest_whole_word)
        self.assertLess(scoring.HALF_WORD_TEXT_PTS, weakest_whole_word)

    def test_lower_fields_add_a_capped_share(self):
        hits = {"meta": False, "summary": True, "ai_keywords": True, "toc": True}
        self.assertAlmostEqual(scoring.secondary_field_bonus("summary", 36.0, hits), 0.30 * 36.0)  # capped below 0.25 * 52
        self.assertAlmostEqual(scoring.secondary_field_bonus("toc", 22.0, hits), 0.0)
        self.assertAlmostEqual(scoring.secondary_field_bonus("title", 68.0, {"meta": True, "summary": True}), 0.25 * 80.0)


if __name__ == "__main__":
    unittest.main()
