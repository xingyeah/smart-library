"""Title repair helpers: real English title from a file name, and the checks on DeepSeek's translations."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import repair_titles as rt  # noqa: E402


class EnglishTitleFromPathTest(unittest.TestCase):
    def test_strips_fake_chinese_number_and_author(self):
        cases = {
            "/k/004.In the Garden of Beasts (Erik Larson) (认知升级与高效生活指南).mobi": "In the Garden of Beasts",
            "/p/Geometry Through History (历史长河中的几何学).pdf": "Geometry Through History",
            "/p/Beginners The Joy of Lifelong Learning (Tom Vanderbilt).epub": "Beginners The Joy of Lifelong Learning",
            "/p/Fluent Forever (Revised Edition) (Gabriel Wyner).epub": "Fluent Forever (Revised Edition)",
            "/p/How Big Things Get Done (Bent Flyvbjerg, Dan Gardner).epub": "How Big Things Get Done",
            "/p/Calculus (Volume 2, 3rd Edition).pdf": "Calculus (Volume 2, 3rd Edition)",
            "/p/思考，快与慢.epub": "",  # a Chinese book: nothing to recover, caller keeps the current title
        }
        for path, expected in cases.items():
            self.assertEqual(rt.english_title_from_path(path), expected, path)

    def test_english_part_and_new_title(self):
        self.assertEqual(rt.english_part_of("数学 (Algebra I)"), "Algebra I")
        self.assertEqual(rt.new_title("Algebra I", "代数（一）"), "代数（一） (Algebra I)")
        self.assertEqual(rt.new_title("Algebra I", ""), "Algebra I")


class AcceptTranslationsTest(unittest.TestCase):
    def test_rejects_foreign_ids_non_chinese_long_and_names_shared_by_different_works(self):
        taken = {"数学理论与方法": rt.work_key("Some Other Book")}
        batch = {1: "In the Garden of Beasts", 2: "Algebra I", 3: "Algebra", 4: "Long", 5: "Algebra II", 6: "Topology",
                 7: "The Selfish Gene", 8: "the selfish gene."}
        reply = {"titles": {"1": "《野兽花园》", "2": "数学理论与方法", "3": "Algebra", "4": "长" * 41, "5": "代数", "6": "代数",
                            "7": "自私的基因", "8": "自私的基因", "777": "别的书", "x": "坏编号"}}
        accepted = rt.accept_translations(reply, batch, taken)
        # 6 is a different work repeating 5's name; 8 is another copy of 7 and may share it
        self.assertEqual(accepted, {1: "野兽花园", 5: "代数", 7: "自私的基因", 8: "自私的基因"})
        self.assertEqual(taken["代数"], rt.work_key("Algebra II"))

if __name__ == "__main__":
    unittest.main()
