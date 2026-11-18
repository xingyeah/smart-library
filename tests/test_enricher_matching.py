"""External book lookups must find the right book or nothing: search by the English original, verify the match."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from enrichers.base import BookQuery, authors_match, search_title, titles_match  # noqa: E402
from enrichers.douban import pick_suggestion  # noqa: E402


class SearchTitleTest(unittest.TestCase):
    def test_bilingual_titles_are_searched_by_their_english_original(self):
        self.assertEqual(search_title("领导有方：激发、留住和鼓舞团队的5种心态 (Lead Well: 5 Mindsets to Engage)"), "Lead Well: 5 Mindsets to Engage")
        self.assertEqual(search_title("思考，快与慢"), "思考，快与慢")
        self.assertEqual(search_title("同流者（莫拉维亚代表作）"), "同流者（莫拉维亚代表作）")  # Chinese note, not an original
        self.assertEqual(search_title("Fluent Forever (Revised Edition)"), "Fluent Forever (Revised Edition)")


class PickSuggestionTest(unittest.TestCase):
    LEAD_WELL = BookQuery(title="Lead Well: 5 Mindsets to Engage, Retain, and Inspire Your Team", author="Davis, Paula")

    def test_rejects_similar_named_books(self):
        items = [
            {"id": "12242032", "title": "Sleep Well, Lead Well", "author_name": "Patel MD Mba, Yatin J."},
            {"id": "32986554", "title": "Start Student Workbook: Lead Well, Be Well, Do Well", "author_name": ""},
            {"id": "4087855", "title": "领导有方", "author_name": "余世维"},
        ]
        self.assertEqual(pick_suggestion(items, self.LEAD_WELL, by_isbn=False), (None, None))

    def test_accepts_the_same_book_further_down_the_list(self):
        items = [
            {"id": "1", "title": "Sleep Well, Lead Well", "author_name": "Yatin Patel"},
            {"id": "2", "title": "Lead Well", "author_name": "Paula Davis", "pic": "http://img/2.jpg"},
        ]
        self.assertEqual(pick_suggestion(items, self.LEAD_WELL, by_isbn=False), ("2", "http://img/2.jpg"))

    def test_isbn_search_is_trusted(self):
        self.assertEqual(pick_suggestion([{"id": "9", "title": "x"}], self.LEAD_WELL, by_isbn=True)[0], "9")

    def test_chinese_title_with_wrong_author_is_rejected(self):
        query = BookQuery(title="领导有方", author="Davis, Paula")
        self.assertEqual(pick_suggestion([{"id": "4087855", "title": "领导有方", "author_name": "余世维"}], query, False), (None, None))
        self.assertEqual(pick_suggestion([{"id": "5", "title": "三体", "author_name": "刘慈欣"}], BookQuery(title="三体", author="刘慈欣"), False)[0], "5")


class MatchRulesTest(unittest.TestCase):
    def test_titles_and_authors(self):
        self.assertTrue(titles_match("Lead Well", "Lead Well: 5 Mindsets"))
        self.assertFalse(titles_match("Sleep Well, Lead Well", "Lead Well: 5 Mindsets"))
        self.assertTrue(authors_match("Paula Davis", "Davis, Paula"))
        self.assertTrue(authors_match("", "Davis, Paula"))
        self.assertTrue(authors_match("[美] 刘慈欣", "刘慈欣"))
        self.assertTrue(authors_match("劉慈欣", "刘慈欣"))  # traditional spelling on Douban
        self.assertFalse(authors_match("余世维", "Davis, Paula"))


if __name__ == "__main__":
    unittest.main()
