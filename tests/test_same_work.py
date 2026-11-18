"""Same-work verdicts for two files of one record (plan section 3), including the v2.1 test matrix."""

import itertools
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from same_work import (  # noqa: E402
    DIFFERENT, SAME, UNCERTAIN, canonical_title, file_facts, is_placeholder_title, judge_same_work,
    normalize_isbn, plan_book_split,
)

_ids = itertools.count(1)


def row(name, **fields):
    base = {"id": next(_ids), "original_path": f"/src/{name}", "archived_path": "", "file_hash": "", "file_size": 1,
            "isbn": "", "embedded_title": "", "embedded_author": ""}
    return {**base, **fields}


def judge(a, b):
    return judge_same_work(file_facts(a), file_facts(b))


class CanonicalTitleTest(unittest.TestCase):
    def test_normalizing(self):
        self.assertEqual(canonical_title("The Great Gatsby"), "great gatsby")
        self.assertEqual(canonical_title("Hackers.and.Painters"), "hackers and painters")
        self.assertEqual(canonical_title("E-Commerce: A Primer"), "ecommerce")
        self.assertEqual(canonical_title("2001: A Space Odyssey"), "2001 a space odyssey")
        self.assertEqual(canonical_title("C++: The Basics"), "c the basics")

    def test_placeholders(self):
        for title in ("Microsoft Word - BL01x01.doc", "BL01x01", "0042", "untitled", "-", "x"):
            self.assertTrue(is_placeholder_title(title), title)
        for title in ("Inside Palantir", "原则", "Thinking"):
            self.assertFalse(is_placeholder_title(title), title)

    def test_isbn_forms_compare_equal(self):
        self.assertEqual(normalize_isbn("0-306-40615-2"), "9780306406157")
        self.assertEqual(normalize_isbn("978-0-306-40615-7"), "9780306406157")
        self.assertEqual(normalize_isbn("n/a"), "")


class MatrixTest(unittest.TestCase):
    """TC-01..TC-07 of the v2.1 design; TC-04 and TC-06 come out uncertain on purpose (see comments)."""

    def test_bilingual_names_with_different_subtitles(self):
        self.assertEqual(judge(row("Inside Palantir (J. Hayden Elsen) (解密Palantir：神秘科技巨头).epub"),
                               row("Inside Palantir_ How a Secretive Tech Tita - Elsen, J. Hayden.pdf")), SAME)

    def test_author_bracket_vs_made_up_chinese_bracket(self):
        self.assertEqual(judge(row("Managing Negotiations (Jerome Joseph).epub"),
                               row("Managing Negotiations (创业管理与战略执行方法).epub")), SAME)

    def test_leading_article(self):
        self.assertEqual(judge(row("The Great Gatsby (F. Scott Fitzgerald).epub"), row("Great Gatsby - Fitzgerald.pdf")), SAME)

    def test_colon_in_file_name_is_cut_by_the_name_parser(self):
        # The file-name parser reads '2001: …' as title '2001', a number: only hash or ISBN may join it
        self.assertEqual(judge(row("2001: A Space Odyssey (Arthur C. Clarke).epub"), row("2001 A Space Odyssey.pdf")), UNCERTAIN)

    def test_shared_made_up_tag_does_not_join_books(self):
        self.assertEqual(judge(row("Hackers.and.Painters (青年成长探索与人文读本).pdf"),
                               row("unmasking the face (青年成长探索与人文读本).pdf")), DIFFERENT)

    def test_short_title_with_one_author_missing_is_for_the_user(self):
        self.assertEqual(judge(row("Thinking (未知作者).epub"), row("Thinking (Daniel Kahneman).pdf")), UNCERTAIN)

    def test_embedded_title_rescues_a_name_without_title(self):
        self.assertEqual(judge(row("Elsen, J Hayden (商业管理).pdf", embedded_title="Inside Palantir"),
                               row("Inside Palantir (J. Hayden Elsen).epub")), SAME)


class RuleTest(unittest.TestCase):
    def test_hash_and_isbn_beat_titles(self):
        self.assertEqual(judge(row("BL01x01.pdf", file_hash="h"), row("Other.pdf", file_hash="h")), SAME)
        self.assertEqual(judge(row("BL01x01.pdf", isbn="0-306-40615-2"), row("BL01x02.pdf", isbn="9780306406157")), SAME)

    def test_placeholder_names_are_never_joined_by_title(self):
        self.assertEqual(judge(row("BL01x01 (当代社会结构与公共治理).pdf", embedded_title="Microsoft Word - BL01x01.doc"),
                               row("BL01x02 (现代政治与公共政策思辨).pdf", embedded_title="Microsoft Word - BL01x02.doc")), UNCERTAIN)

    def test_same_short_title_by_different_authors(self):
        self.assertEqual(judge(row("Principles (Ray Dalio).epub"), row("Principles (Alfred Marshall).pdf")), DIFFERENT)

    def test_author_name_order_and_traditional_characters(self):
        self.assertEqual(judge(row("Thinking (Daniel Kahneman).epub"), row("Thinking - Kahneman, Daniel.pdf")), SAME)
        self.assertEqual(judge(row("三体.epub", embedded_author="劉慈欣"), row("三体.mobi", embedded_author="刘慈欣")), SAME)

    def test_author_in_two_scripts_is_for_the_user(self):
        self.assertEqual(judge(row("x.epub", embedded_title="思考，快与慢", embedded_author="丹尼尔·卡尼曼"),
                               row("y.epub", embedded_title="思考，快与慢", embedded_author="Daniel Kahneman")), UNCERTAIN)

    def test_title_in_two_scripts_is_for_the_user(self):
        self.assertEqual(judge(row("思考，快与慢.epub"), row("Thinking, Fast and Slow.pdf")), UNCERTAIN)

    def test_edition_notes_spacing_and_cut_names(self):
        self.assertEqual(judge(row("昆虫记(全译美绘本) - 法布尔.azw3"), row("昆虫记 (青少年成长必读经典书系) - 法布尔.epub")), SAME)
        self.assertEqual(judge(row("Go 语言实战 ( etc.).pdf"), row("Go语言实战.epub")), SAME)
        self.assertEqual(judge(row("Topology (James Munkres).pdf"), row("Topology Pearson Edition (James R. Munkres).pdf")), SAME)
        self.assertEqual(judge(row("Pacific Crucible War at Sea in the Pacifi (Ian W. Toll).azw3"),
                               row("Pacific crucible war at sea in the Pacific 1941 1942.epub")), SAME)
        self.assertEqual(judge(row("Psychology A Very Short Introduction (Gillian Butler).pdf"),
                               row("Infinity A Very Short Introduction (Ian Stewart).epub")), DIFFERENT)

    def test_cases_from_the_first_preview_review(self):
        # 118: the Chinese edition by the same author stays
        self.assertEqual(judge(row("清醒思考的艺术（精编图文版） - Rolf Dobelli, PhD,.mobi"),
                               row("The Art of Thinking Clearly (Rolf Dobelli) (艺术思维方法).epub")), SAME)
        # 2925: an apostrophe
        self.assertEqual(judge(row("Mao's Great Famine (Frank Dikotter).pdf"),
                               row("Maos Great Famine The History of Chinas Most Devastating Catastrophe (Frank Dikötter).epub")), SAME)
        # 7205: download-site prefix
        self.assertEqual(judge(row("ebooksclub org Phantoms in the brain probing the mysteries of the human mind (大脑科学).pdf"),
                               row("Phantoms in the Brain Probing the Mysteries of the Human Mind (大脑科学).pdf")), SAME)
        # 8686, 19158, 18981: the other title plus an edition note, an author, or the title in brackets
        self.assertEqual(judge(row("追寻记忆的痕迹.pdf"), row("追寻记忆的痕迹-文字版.pdf")), SAME)
        self.assertEqual(judge(row("意料之外的绞刑和其他数学娱乐-[美]马丁·加德纳.pdf"), row("意料之外的绞刑和其他数学娱乐.pdf")), SAME)
        self.assertEqual(judge(row("[概率论沉思录]原版.pdf"), row("概率论沉思录.mobi")), SAME)
        self.assertEqual(judge(row("数学分析.pdf"), row("数学分析习题集.pdf")), DIFFERENT)

    def test_download_site_watermark_is_no_title_or_author(self):
        # 210: every magazine of one site carries the site name as its embedded title and author
        site = {"embedded_title": "downmagaz.net", "embedded_author": "downmagaz.net"}
        self.assertEqual(judge(row("MIT Sloan Management Review - Winter 2025 (管理学).pdf", **site),
                               row("Barron's 20260824 (巴伦周刊金融投资导读).pdf", **site)), DIFFERENT)

    def test_different_isbns_alone_do_not_split_editions(self):
        self.assertEqual(judge(row("Deep Work (Cal Newport).epub", isbn="9781455586691"),
                               row("Deep Work - Newport.pdf", isbn="9780349411903")), SAME)


class PlanTest(unittest.TestCase):
    def test_anchor_is_the_majority_not_the_primary(self):
        stray = row("Unmasking the Face (Paul Ekman).pdf", file_size=999)
        a, b = row("Hackers and Painters (Paul Graham).epub"), row("Hackers and Painters - Graham.pdf")
        plan = plan_book_split([stray, a, b], "Hackers and Painters")
        self.assertIn(plan["anchor"], (a["id"], b["id"]))
        self.assertEqual(plan[DIFFERENT], [stray["id"]])
        self.assertEqual(len(plan[SAME]), 1)

    def test_record_title_outranks_the_majority(self):
        own = row("Depression A Very Short Introduction (Jan Scott).epub")
        strays = [row("Wittgenstein A Very Short Introduction (A. C. Grayling).pdf"),
                  row("Wittgenstein A Very Short Introduction (Grayling, Anthony C).epub")]
        plan = plan_book_split([*strays, own], "抑郁症简论 (Depression A Very Short Introduction)")
        self.assertEqual(plan["anchor"], own["id"])
        self.assertEqual(sorted(plan[DIFFERENT]), sorted(s["id"] for s in strays))


if __name__ == "__main__":
    unittest.main()
