"""Validation rules for DeepSeek-enriched metadata (backend/enrich_ai.py); no network."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import enrich_ai as e  # noqa: E402


class CleanTitleTest(unittest.TestCase):
    def test_marketing_blurbs_removed(self):
        self.assertEqual(e.clean_title_for("癌症传（新版）（普利策奖作品，文津奖推荐，医学人文领域经典畅销著作）", "癌症传"), "癌症传")

    def test_no_additions_or_script_conversion(self):
        self.assertEqual(e.clean_title_for("加密的真相", "加密的真相：数字资产如何改变世界"), "加密的真相")
        self.assertEqual(e.clean_title_for("區塊鏈革命", "区块链革命"), "區塊鏈革命")

    def test_clean_titles_and_english_originals_kept(self):
        title = "进阶 (Advanced Data Science and Analytics with Python)"
        self.assertEqual(e.clean_title_for(title, "进阶"), title)
        self.assertEqual(e.clean_title_for("思维认知书系（套装10册）", "思维认知书系"), "思维认知书系（套装10册）")


class ValidateTest(unittest.TestCase):
    def test_unknown_ids_and_out_of_vocab_tags_dropped(self):
        titles = {7: "火星编年史"}
        self.assertIsNone(e.validate({"id": 8, "level": "入门"}, titles))
        row = e.validate(
            {"id": "7", "level": "大师级", "kind": "小说文学", "category": "火星文学", "keywords": ["火星", "", "科幻"]},
            titles,
        )
        self.assertEqual((row["level"], row["kind"], row["ai_category"]), ("", "小说文学", ""))
        self.assertEqual(json.loads(row["keywords"]), ["火星", "科幻"])


if __name__ == "__main__":
    unittest.main()
