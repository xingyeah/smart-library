import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import book_enrichment_service as bes  # noqa: E402
import build_bge_index  # noqa: E402
import database  # noqa: E402
import semantic_models  # noqa: E402


class PassageRecipeTest(unittest.TestCase):
    def test_passage_recipe_prioritizes_ai_fields_and_filters_core_points_by_source(self) -> None:
        long_summary = "科幻史诗简介。" + ("核心设定" * 120)
        ai_row = {
            "title": "原标题含广告",
            "clean_title": "银河帝国合集",
            "subtitle": "经典全本",
            "author": "阿西莫夫",
            "category": "文学小说与艺术",
            "ai_category": "文学小说与艺术",
            "subfield": "科幻文学",
            "kind": "小说文学",
            "level": "普及",
            "keywords": json.dumps(["阿西莫夫", "心理史学", "机器人"]),
            "summary": long_summary,
            "summary_source": "ai_refreshed",
            "core_points": json.dumps(["心理史学预测兴衰", "机器人三定律", "时间旅行悖论"]),
            "target_audience": "独有受众标签不应进入向量XYZ",
            "toc_text": "序言 第一章 心理史学 第二章 基地危机",
        }
        passage = build_bge_index.build_book_passage(ai_row)
        self.assertIn("《银河帝国合集》", passage)
        self.assertNotIn("原标题含广告", passage)
        self.assertIn("领域：文学小说与艺术/科幻文学", passage)
        self.assertIn("类型：小说文学/普及", passage)
        self.assertIn("关键词：阿西莫夫、心理史学、机器人。", passage)
        self.assertIn("看点：心理史学预测兴衰；机器人三定律；时间旅行悖论", passage)
        self.assertNotIn("独有受众标签不应进入向量XYZ", passage)
        self.assertLess(passage.index("看点："), passage.index("章节："))
        self.assertLess(passage.index("关键词："), passage.index("看点："))
        self.assertEqual(
            passage.count("核心设定"),
            len(long_summary[: build_bge_index.SUMMARY_MAX_CHARS].split("核心设定")) - 1,
        )

        xlsx_row = dict(ai_row, summary_source="xlsx_curated", core_points=json.dumps(["Chapter 1", "Half-Title"]))
        xlsx_passage = build_bge_index.build_book_passage(xlsx_row)
        self.assertIn("关键词：阿西莫夫、心理史学、机器人。", xlsx_passage)
        self.assertNotIn("看点：", xlsx_passage)
        self.assertNotIn("Chapter 1", xlsx_passage)

    def test_sync_bge_index_uses_512_max_length_and_ai_meta_join(self) -> None:
        self.assertEqual(build_bge_index.EMBED_MAX_LENGTH, 512)
        self.assertEqual(build_bge_index.PASSAGE_SCHEMA_VERSION, 2)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "test.db"
            npy_path = root / "bge_m3_vectors.npy"
            meta_path = root / "bge_m3_meta.json"
            database.init_db(db_path)

            conn = database.get_conn(db_path)
            try:
                cur = conn.execute(
                    "INSERT INTO books (dedup_key, title, norm_title, author, category, summary, summary_source, core_points) "
                    "VALUES ('k1', 'RawTitle', 'rawtitle', 'Author', '文学', 'AI 重写简介', 'ai_refreshed', '[\"核心看点\"]')"
                )
                bid = int(cur.lastrowid)
                conn.execute(
                    "INSERT INTO book_ai_meta (book_id, clean_title, keywords, level, kind, ai_category, subfield) "
                    "VALUES (?, 'CleanTitle', '[\"关键标签\"]', '进阶', '学术理论', '文学', '当代小说')",
                    (bid,),
                )
                conn.commit()
            finally:
                conn.close()

            calls = []

            class FakeEngine:
                def is_embedder_ready(self) -> bool:
                    return True

                def encode_texts(self, texts, max_length=144, batch_size=8):
                    calls.append((list(texts), max_length, batch_size))
                    vec = np.zeros((len(texts), build_bge_index.EMBED_DIM), dtype=np.float32)
                    vec[:, 0] = 1.0
                    return vec

                def load_vector_index(self) -> bool:
                    return True

            with (
                mock.patch.object(build_bge_index, "get_conn", lambda: database.get_conn(db_path)),
                mock.patch.object(build_bge_index, "BGE_VEC_NPY_PATH", npy_path),
                mock.patch.object(build_bge_index, "BGE_VEC_META_PATH", meta_path),
                mock.patch.object(build_bge_index, "get_bge_engine", lambda: FakeEngine()),
            ):
                res = build_bge_index.sync_bge_m3_index()

            self.assertTrue(res["ok"])
            self.assertEqual(calls[0][1], 512)
            self.assertIn("关键标签", calls[0][0][0])
            self.assertIn("核心看点", calls[0][0][0])
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            self.assertEqual(meta["max_length"], 512)
            self.assertEqual(meta["passage_version"], 2)
            self.assertEqual(
                meta["passage_hashes"],
                [build_bge_index.compute_passage_hash(calls[0][0][0])],
            )
            self.assertEqual(list(root.glob(".*.tmp.*")), [])


class InMemoryUpsertTest(unittest.TestCase):
    def test_upsert_book_vector_updates_and_appends_with_new_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            npy_path = root / "vecs.npy"
            meta_path = root / "meta.json"
            mat = np.zeros((2, semantic_models.EMBED_DIM), dtype=np.float32)
            mat[0, 0] = 1.0
            mat[1, 1] = 1.0
            np.save(str(npy_path), mat)
            meta_path.write_text(json.dumps({"book_ids": [10, 20]}), encoding="utf-8")

            with (
                mock.patch.object(semantic_models, "BGE_VEC_NPY_PATH", npy_path),
                mock.patch.object(semantic_models, "BGE_VEC_META_PATH", meta_path),
            ):
                engine = semantic_models.BgeM3Engine()
                self.assertTrue(engine.load_vector_index())
                _, old_mat = engine.get_book_matrix()

                new_v10 = np.zeros(semantic_models.EMBED_DIM, dtype=np.float32)
                new_v10[5] = 2.0
                self.assertTrue(engine.upsert_book_vector(10, new_v10))

                _, updated_mat = engine.get_book_matrix()
                self.assertIsNot(updated_mat, old_mat)
                self.assertAlmostEqual(float(old_mat[0, 0]), 1.0)
                hits = engine.dense_search_by_vector(new_v10, top_k=2)
                self.assertEqual(hits[0][0], 10)
                self.assertAlmostEqual(hits[0][1], 1.0, places=5)

                new_v30 = np.zeros(semantic_models.EMBED_DIM, dtype=np.float32)
                new_v30[7] = 3.0
                self.assertTrue(engine.upsert_book_vector(30, new_v30))
                ids, appended_mat = engine.get_book_matrix()
                self.assertEqual(ids.tolist(), [10, 20, 30])
                self.assertEqual(appended_mat.shape, (3, semantic_models.EMBED_DIM))
                hits2 = engine.dense_search_by_vector(new_v30, top_k=2)
                self.assertEqual(hits2[0][0], 30)
                self.assertAlmostEqual(hits2[0][1], 1.0, places=5)


class EnrichmentRefreshHookTest(unittest.TestCase):
    def test_refresh_book_ai_metadata_updates_memory_vector_before_spawn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "enrich.db"
            npy_path = root / "vecs.npy"
            meta_path = root / "meta.json"
            database.init_db(db_path)

            conn = database.get_conn(db_path)
            try:
                cur = conn.execute(
                    "INSERT INTO books (dedup_key, title, norm_title, author, category, summary) "
                    "VALUES ('e1', 'Book', 'book', 'Auth', '历史', '旧简介')"
                )
                bid = int(cur.lastrowid)
                conn.commit()
            finally:
                conn.close()

            mat = np.zeros((1, semantic_models.EMBED_DIM), dtype=np.float32)
            mat[0, 0] = 1.0
            np.save(str(npy_path), mat)
            meta_path.write_text(json.dumps({"book_ids": [bid]}), encoding="utf-8")

            engine = semantic_models.BgeM3Engine()
            v_new = np.zeros((1, semantic_models.EMBED_DIM), dtype=np.float32)
            v_new[0, 9] = 1.0
            engine.is_embedder_ready = lambda: True
            engine.encode_texts = lambda texts, max_length=144, batch_size=8: v_new

            with (
                mock.patch.object(semantic_models, "BGE_VEC_NPY_PATH", npy_path),
                mock.patch.object(semantic_models, "BGE_VEC_META_PATH", meta_path),
                mock.patch.object(build_bge_index, "get_bge_engine", lambda: engine),
            ):
                self.assertTrue(engine.load_vector_index())
                ok = build_bge_index.refresh_book_vector_in_memory(
                    bid, conn_factory=lambda: database.get_conn(db_path)
                )
                self.assertTrue(ok)
                hits = engine.dense_search_by_vector(v_new[0], top_k=1)
                self.assertEqual(hits[0][0], bid)

            order = []
            enriched = {
                "clean_title": "Book",
                "category": "历史",
                "subfield": "世界史",
                "level": "普及",
                "kind": "通识科普",
                "keywords": ["世界史"],
                "summary": "更新后的中文详尽内容简介超过三十五个字以测试提炼写回流程是否正常执行。",
                "core_points": ["要点一"],
                "target_audience": "历史读者",
            }
            with (
                mock.patch.object(bes, "get_conn", lambda: database.get_conn(db_path)),
                mock.patch.object(bes, "_extract_sample_content", return_value="sample"),
                mock.patch.object(bes, "_call_llm_enrichment", return_value=enriched),
                mock.patch.object(bes, "_start_cover_fetch"),
                mock.patch.object(
                    bes,
                    "refresh_book_vector_in_memory",
                    side_effect=lambda b, **kw: order.append(("mem", b)) or True,
                ),
                mock.patch.object(bes, "spawn_reindex", side_effect=lambda ids: order.append(("spawn", set(ids)))),
                mock.patch("catalog_sync.enqueue_catalog_sync"),
            ):
                bes.refresh_book_ai_metadata(bid)
            self.assertEqual(order, [("mem", bid), ("spawn", {bid})])


if __name__ == "__main__":
    unittest.main()
