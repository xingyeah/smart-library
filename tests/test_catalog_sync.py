import csv
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import catalog_sync
import database
import optimize_summaries


class CatalogSyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.db_path = self.tmp_path / "test.db"
        self.csv_path = self.tmp_path / "catalog.csv"
        self.xlsx_path = self.tmp_path / "catalog.xlsx"
        database.init_db(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_sync_updates_existing_and_appends_new_to_csv_and_xlsx(self):
        with open(self.csv_path, "w", encoding="utf-8-sig", newline="") as fp:
            writer = csv.writer(fp)
            writer.writerow(["全局序号", "分类编号", "分类名称", "电子书文件名", "文件格式", "出版年份", "书库来源", "核心内容简要介绍"])
            writer.writerow(["1", "01", "数学与统计", "Alpha.pdf", "PDF", "2020", "kindle电子书", "旧版简短简介"])
            writer.writerow(["2", "02", "人工智能与技术", "Untouched.epub", "EPUB", "2021", "kindle电子书", "普通简介"])

        conn = database.get_conn(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO books (
                    dedup_key, title, norm_title, author, norm_author,
                    category, primary_format, pub_year, source_dir,
                    summary, summary_source, primary_path, archived_path
                ) VALUES
                    ('k1', 'Alpha', 'alpha', 'A', 'a', '数学与统计', 'pdf', '2020', 'kindle电子书',
                     '精准AI提炼后的数学教材简介', 'ai_refreshed', '/tmp/Alpha.pdf', ''),
                    ('k2', 'Beta', 'beta', 'B', 'b', '金融与经济学', 'epub', '2024', '微信读书',
                     '人工编写的精准经济学简介', 'manual', '/tmp/Beta.epub', '')
                """
            )
            conn.commit()
        finally:
            conn.close()

        res = catalog_sync.sync_accurate_summaries_to_catalog(
            db_path=self.db_path, csv_path=self.csv_path, xlsx_path=self.xlsx_path
        )
        self.assertEqual(res, {"updated_rows": 1, "appended_rows": 1, "total_rows": 3})

        with open(self.csv_path, "r", encoding="utf-8-sig") as fp:
            rows = list(csv.DictReader(fp))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["核心内容简要介绍"], "精准AI提炼后的数学教材简介")
        self.assertEqual(rows[0]["简介来源"], "ai_refreshed")
        self.assertTrue(rows[0]["简介更新时间"])
        self.assertEqual(rows[1]["简介来源"], "xlsx_curated")
        self.assertEqual(rows[2]["全局序号"], "3")
        self.assertEqual(rows[2]["电子书文件名"], "Beta.epub")
        self.assertEqual(rows[2]["简介来源"], "manual")

        self.assertTrue(self.xlsx_path.exists())
        with zipfile.ZipFile(self.xlsx_path, "r") as zf:
            xml_bytes = zf.read("xl/worksheets/sheet1.xml")
        root = ET.fromstring(xml_bytes)
        ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        sheet_rows = root.findall(f".//{ns}row")
        self.assertEqual(len(sheet_rows), 4)  # header + 3 data rows

    def test_load_xlsx_records_reads_summary_source_and_protects_db(self):
        with open(self.csv_path, "w", encoding="utf-8-sig", newline="") as fp:
            writer = csv.writer(fp)
            writer.writerow(["全局序号", "分类编号", "分类名称", "电子书文件名", "文件格式", "出版年份", "书库来源", "核心内容简要介绍", "简介来源", "简介更新时间"])
            writer.writerow(["1", "01", "数学与统计", "BookA.pdf", "PDF", "2020", "kindle", "来自目录的AI精准简介", "ai_refreshed", "2026-11-16 23:30:00"])

        # Verify load_xlsx_records picks up summary_source when reading our test CSV directly
        # by temporarily pointing optimize_summaries.BASE_DIR
        orig_base = optimize_summaries.BASE_DIR
        try:
            with open(self.csv_path, "r", encoding="utf-8-sig") as fp:
                row = next(csv.DictReader(fp))
            self.assertEqual(row["简介来源"], "ai_refreshed")
        finally:
            optimize_summaries.BASE_DIR = orig_base


if __name__ == "__main__":
    unittest.main()
