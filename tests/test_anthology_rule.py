"""Which books count as box sets: the regex for packaging words, plus the AI's 套装合集 type."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from classic_scorer import is_anthology_book  # noqa: E402


class AnthologyRuleTests(unittest.TestCase):
    def test_single_volume_of_a_publisher_series_is_not_a_box_set(self):
        self.assertEqual(is_anthology_book("物种起源 (汉译世界学术名著丛书)", "", "学术理论"), 0)

    def test_packaging_words_mark_a_box_set_whatever_the_ai_type(self):
        self.assertEqual(is_anthology_book("奥威尔小说全集（套装共6册）", "", "小说文学"), 1)

    def test_ai_box_set_type_marks_a_set_without_packaging_words(self):
        self.assertEqual(is_anthology_book("尤瓦尔简史三部曲", "", "套装合集"), 1)

    def test_untyped_book_falls_back_to_the_regex(self):
        self.assertEqual(is_anthology_book("某某作品集", "", ""), 1)
        self.assertEqual(is_anthology_book("某某传", "", ""), 0)


if __name__ == "__main__":
    unittest.main()
