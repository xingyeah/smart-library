"""Author links on the book detail and the shelf's 'all books by this author' filter."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import database
from search.authors import author_key, author_links, split_authors
from search.shelf import search_empty_shelf
from search.types import ShelfFilters, ShelfQuery


class AuthorSplitTests(unittest.TestCase):
    def test_people_are_split_and_tags_roles_dropped(self):
        self.assertEqual(split_authors("Robert E. McGrath / Ryan M. Niemiec"), ["Robert E. McGrath", "Ryan M. Niemiec"])
        self.assertEqual(split_authors("塔莉・沙羅特、凱斯・桑思坦"), ["塔莉・沙羅特", "凱斯・桑思坦"])
        self.assertEqual(split_authors("[美]丹·西蒙斯"), ["丹·西蒙斯"])
        self.assertEqual(split_authors("丹尼尔·卡尼曼 著"), ["丹尼尔·卡尼曼"])
        self.assertEqual(split_authors("Drew Ramsey, M.D."), ["Drew Ramsey"])
        self.assertEqual(split_authors("Jonathan Martineau and David Broder"), ["Jonathan Martineau", "David Broder"])

    def test_surname_first_comma_is_one_person(self):
        self.assertEqual(split_authors("Efron, Bradley"), ["Efron, Bradley"])
        self.assertEqual(split_authors("Martin J Osborne, Ariel Rubinstein"), ["Martin J Osborne", "Ariel Rubinstein"])

    def test_key_ignores_case_spacing_script_and_order(self):
        self.assertEqual(author_key("Efron, Bradley"), author_key("bradley  efron"))
        self.assertEqual(author_key("塔莉・沙羅特"), author_key("塔莉·沙罗特"))

    def test_placeholders_and_garbled_names_are_not_links(self):
        self.assertEqual(author_links("未知作者"), [{"name": "未知作者", "linkable": False}])
        self.assertFalse(author_links("4<8=8AB@0B>@")[0]["linkable"])
        self.assertTrue(author_links("Joe Navarro")[0]["linkable"])


class AuthorFilterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "authors.db"
        database.init_db(self.db_path)
        conn = database.get_conn(self.db_path)
        for key, title, author in (
            ("k1", "掌控人际连接", "Joe Navarro"),
            ("k2", "合著的书", "Marvin Karlins / joe navarro"),
            ("k3", "别人的书", "Joe Navarrow"),
            ("k4", "繁体署名", "塔莉・沙羅特、凱斯・桑思坦"),
        ):
            conn.execute(
                "INSERT INTO books (dedup_key, title, norm_title, author, norm_author, primary_format, primary_path) "
                "VALUES (?, ?, ?, ?, ?, 'epub', ?)",
                (key, title, title, author, author, f"/tmp/{key}"),
            )
        conn.commit()
        conn.close()
        patcher = mock.patch("search.shelf.get_conn", lambda: database.get_conn(self.db_path))
        patcher.start()
        self.addCleanup(patcher.stop)

    def titles_by(self, author):
        res = search_empty_shelf(ShelfQuery(filters=ShelfFilters(author=author)))
        return sorted(b["title"] for b in res["items"]), res["total"]

    def test_lists_every_book_naming_the_author_and_no_other(self):
        self.assertEqual(self.titles_by("Joe Navarro"), (["合著的书", "掌控人际连接"], 2))

    def test_simplified_click_finds_traditional_byline(self):
        self.assertEqual(self.titles_by("塔莉·沙罗特"), (["繁体署名"], 1))


if __name__ == "__main__":
    unittest.main()
