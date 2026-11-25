import ast
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import database  # noqa: E402
import deepseek_client as ds  # noqa: E402
from http_api import background, state  # noqa: E402
import jobs  # noqa: E402
import migrations  # noqa: E402
from search.filters import build_book_facet_where, parse_language_filter  # noqa: E402
from search.interest import _language_ids  # noqa: E402
from search.types import ShelfFilters  # noqa: E402


class ArchitecturalRatchetTest(unittest.TestCase):
    def test_no_http_api_imports_outside_transport_and_facade(self) -> None:
        violations = []
        for pyfile in sorted(BACKEND_DIR.rglob("*.py")):
            rel = pyfile.relative_to(BACKEND_DIR)
            if rel.parts[0] == "http_api" or rel == Path("server.py"):
                continue
            tree = ast.parse(pyfile.read_text(encoding="utf-8"), filename=str(pyfile))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "http_api" or alias.name.startswith("http_api."):
                            violations.append(f"{rel}:{node.lineno} imports {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    if mod == "http_api" or mod.startswith("http_api."):
                        violations.append(f"{rel}:{node.lineno} imports from {mod}")
        self.assertEqual(violations, [])

    def test_no_runtime_create_table_outside_database_and_migrations(self) -> None:
        violations = []
        allowed = {Path("database.py"), Path("migrations.py")}
        for pyfile in sorted(BACKEND_DIR.rglob("*.py")):
            rel = pyfile.relative_to(BACKEND_DIR)
            if rel in allowed:
                continue
            src = pyfile.read_text(encoding="utf-8")
            if "create table" in src.lower():
                violations.append(str(rel))
        self.assertEqual(violations, [])


class JobsDecouplingTest(unittest.TestCase):
    def test_jobs_shares_lock_and_reexports_with_http_api(self) -> None:
        self.assertIs(jobs._reindex_lock, state._reindex_lock)
        self.assertIs(background.spawn_reindex, jobs.spawn_reindex)
        self.assertIs(background.reindex_in_background, jobs.reindex_in_background)

        with mock.patch.object(jobs, "rebuild_vector_cache") as rebuild:
            jobs.reindex_in_background([42, 7])
        rebuild.assert_called_once_with(reencode_ids=[42, 7])


class MigrationsV9Test(unittest.TestCase):
    def test_v8_to_v9_creates_consolidated_tables_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "v9.db"
            database.init_db(db_path)
            conn = database.get_conn(db_path)
            try:
                for tbl in ("ai_usage", "ai_rerank_cache", "title_repair"):
                    conn.execute(f"DROP TABLE IF EXISTS {tbl}")
                conn.execute("PRAGMA user_version = 8")
                conn.commit()

                migrations.apply_migrations(conn)
                migrations.apply_migrations(conn)
                conn.commit()

                version = conn.execute("PRAGMA user_version").fetchone()[0]
                self.assertEqual(version, 11)
                tables = {
                    r["name"]
                    for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                }
                self.assertTrue({"ai_usage", "ai_rerank_cache", "title_repair"}.issubset(tables))
            finally:
                conn.close()

            with mock.patch.object(ds, "get_conn", lambda: database.get_conn(db_path)):
                ds.record_usage("search", ds.FLASH_MODEL, {"prompt_tokens": 1000, "completion_tokens": 200})
                self.assertGreater(ds.today_cost(("search",)), 0.0)


class UnifiedSearchFiltersTest(unittest.TestCase):
    def test_facet_where_aliased_and_unaliased_parity(self) -> None:
        f = ShelfFilters(
            category="数学",
            language="zh-CN",
            fmt="EPUB",
            source_dir="lib",
            reading_status="reading",
            only_favorite=True,
            pub_period="1900s",
            has_rating=True,
        )
        unaliased, params_u = build_book_facet_where(f)
        aliased, params_a = build_book_facet_where(f, table_alias="b")

        self.assertEqual(params_u, params_a)
        self.assertEqual(unaliased[0], "is_deleted = 0")
        self.assertEqual(aliased[0], "b.is_deleted = 0")
        self.assertIn("COLLATE NOCASE IN", " ".join(unaliased))
        self.assertIn("pub_year GLOB '19[0-9][0-9]*'", unaliased)
        self.assertIn("b.pub_year GLOB '19[0-9][0-9]*'", aliased)
        self.assertIn("em.book_id = books.id", " ".join(unaliased))
        self.assertIn("em.book_id = b.id", " ".join(aliased))
        self.assertEqual(parse_language_filter("全部"), [])

    def test_interest_language_ids_respects_nocase_and_excludes_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "lang.db"
            database.init_db(db_path)
            conn = database.get_conn(db_path)
            try:
                conn.execute(
                    "INSERT INTO books (id, dedup_key, title, norm_title, language, is_deleted) "
                    "VALUES (1, 'k1', 'A', 'a', 'zh-cn', 0), "
                    "(2, 'k2', 'B', 'b', 'zh-CN', 1), "
                    "(3, 'k3', 'C', 'c', 'en', 0)"
                )
                conn.commit()
                self.assertEqual(_language_ids(conn, "zh-CN"), {1})
                self.assertIsNone(_language_ids(conn, ""))
            finally:
                conn.close()


class FrontendStaticGateTest(unittest.TestCase):
    def test_frontend_syntax_and_checkjs_script(self) -> None:
        proc = subprocess.run(
            ["node", str(REPO_ROOT / "scripts" / "check_frontend_js.mjs")],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
        self.assertIn("Syntax check passed", proc.stdout)
        if "compiler:" in proc.stdout:
            self.assertIn("checkJs passed (0 diagnostics", proc.stdout)


if __name__ == "__main__":
    unittest.main()
