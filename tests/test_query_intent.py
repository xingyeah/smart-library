"""Fast unit tests for query parsing and term matching (needs data/library.db, not the ONNX models)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from config import DB_PATH  # noqa: E402

if DB_PATH.exists():
    import search_engine as se  # noqa: E402


class HasTermTest(unittest.TestCase):
    @unittest.skipUnless(DB_PATH.exists(), "library.db not found")
    def test_short_ascii_terms_match_whole_words(self):
        self.assertTrue(se._has_term("ai", "ai营销实战手册"))
        self.assertTrue(se._has_term("ai", "using ai in marketing"))
        self.assertFalse(se._has_term("ai", "brain first business"))
        self.assertFalse(se._has_term("ai", "supply chain 5.0"))
        self.assertTrue(se._has_term("c++", "c++ primer"))

    @unittest.skipUnless(DB_PATH.exists(), "library.db not found")
    def test_long_and_cjk_terms_use_substring(self):
        self.assertTrue(se._has_term("python", "python编程三剑客"))
        self.assertTrue(se._has_term("营销", "数字营销战略"))


@unittest.skipUnless(DB_PATH.exists(), "library.db not found")
class QueryIntentTest(unittest.TestCase):
    def intent(self, q):
        return se.analyze_query_intent(q)

    def test_author_name_kept_as_anchor(self):
        i = self.intent("刘慈欣科幻")
        self.assertIn("刘慈欣", i["author_terms"])
        self.assertIn("科幻", i["primary_terms"])

    def test_transliterated_author_surname(self):
        i = self.intent("赫拉利写的历史书")
        self.assertEqual(i["primary_terms"], ["赫拉利"])

    def test_residual_after_domain_word(self):
        self.assertIn("斯多葛", self.intent("斯多葛哲学")["primary_terms"])
        self.assertIn("二战", self.intent("二战历史")["primary_terms"])

    def test_compound_terms_not_fragmented(self):
        terms = self.intent("价值投资经典")["primary_terms"]
        self.assertIn("价值投资", terms)
        self.assertNotIn("价值", terms)

    def test_filler_words_not_core_terms(self):
        i = self.intent("基于ai的营销方法、理念")
        self.assertNotIn("基于", i["primary_terms"])
        self.assertIn("ai", i["primary_terms"])
        self.assertIn("营销", i["primary_terms"])
        self.assertIn("理念", i["parent_nouns"])
        self.assertIn("人工智能", i["synonym_terms"])

    def test_disjunction_flag(self):
        self.assertTrue(self.intent("Python或者数据分析")["is_disjunctive"])
        self.assertFalse(self.intent("认知偏差 行为经济学")["is_disjunctive"])

    def test_manage_as_verb_is_not_business(self):
        i = self.intent("如何管理焦虑和情绪")
        self.assertNotIn("管理", i["primary_terms"] + i["parent_nouns"])
        self.assertEqual(i["inferred_categories"][:1], ["心理学与脑科学"])

    def test_beginner_style_detected(self):
        i = self.intent("有没有适合零基础小白入门Python或者数据分析的书，不要太枯燥的")
        self.assertEqual(i["style_mode"], "intro_popsci")

    def test_dynasty_expands_to_eras(self):
        i = self.intent("明朝历史人物")
        self.assertEqual(i["primary_terms"], ["明朝"])
        self.assertIn("万历", i["synonym_terms"])

    def test_crypto_terms_expand_and_aspect_words_are_tier2(self):
        i = self.intent("比特币的底层技术")
        self.assertEqual(i["primary_terms"], ["比特币"])
        self.assertIn("底层", i["parent_nouns"])
        self.assertIn("区块链", i["synonym_terms"])
        i = self.intent("区块链技术原理")
        self.assertIn("区块链", i["primary_terms"])
        self.assertNotIn("区块", i["primary_terms"])
        self.assertIn("原理", i["parent_nouns"])

    def test_how_it_works_asks_use_technical_depth(self):
        self.assertEqual(self.intent("比特币的底层技术")["style_mode"], "technical_depth")
        self.assertEqual(self.intent("区块链技术原理")["style_mode"], "technical_depth")
        self.assertEqual(self.intent("比特币")["style_mode"], "neutral")


if __name__ == "__main__":
    unittest.main()
