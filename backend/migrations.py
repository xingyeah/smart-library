"""SQLite schema migrations for the library database."""

import sqlite3

_MIGRATION_TRIGGER_SQL = """
DROP TRIGGER IF EXISTS books_au;
CREATE TRIGGER books_au AFTER UPDATE ON books
WHEN old.title IS NOT new.title OR old.subtitle IS NOT new.subtitle OR old.author IS NOT new.author
  OR old.translator IS NOT new.translator OR old.series IS NOT new.series OR old.category IS NOT new.category
  OR old.tags IS NOT new.tags OR old.summary IS NOT new.summary OR old.core_points IS NOT new.core_points
  OR old.hyde_queries IS NOT new.hyde_queries OR old.toc_text IS NOT new.toc_text
  OR old.pinyin_search IS NOT new.pinyin_search OR old.isbn IS NOT new.isbn
BEGIN
    INSERT INTO books_fts(
        books_fts, rowid, title, subtitle, author, translator, series,
        category, tags, summary, core_points, hyde_queries,
        toc_text, pinyin_search, isbn
    ) VALUES (
        'delete', old.id, old.title, old.subtitle, old.author, old.translator, old.series,
        old.category, old.tags, old.summary, old.core_points, old.hyde_queries,
        old.toc_text, old.pinyin_search, old.isbn
    );
    INSERT INTO books_fts(
        rowid, title, subtitle, author, translator, series,
        category, tags, summary, core_points, hyde_queries,
        toc_text, pinyin_search, isbn
    ) VALUES (
        new.id, new.title, new.subtitle, new.author, new.translator, new.series,
        new.category, new.tags, new.summary, new.core_points, new.hyde_queries,
        new.toc_text, new.pinyin_search, new.isbn
    );
END;
"""


def _migrate_v1(conn: sqlite3.Connection) -> None:
    """Classic-score/reading-progress columns (idempotent, guarded per column)."""
    existing_cols = {r["name"] for r in conn.execute("PRAGMA table_info(books)").fetchall()}
    if "classic_score" not in existing_cols:
        conn.execute("ALTER TABLE books ADD COLUMN classic_score REAL DEFAULT 0.0")
    if "douban_votes" not in existing_cols:
        conn.execute("ALTER TABLE books ADD COLUMN douban_votes INTEGER DEFAULT 0")
    if "is_anthology" not in existing_cols:
        conn.execute("ALTER TABLE books ADD COLUMN is_anthology INTEGER DEFAULT 0")
    for col, decl in (("last_opened_at", "TEXT"), ("read_progress", "REAL"), ("read_pos", "TEXT")):
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE books ADD COLUMN {col} {decl}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_books_classic ON books(classic_score DESC)")


def _migrate_v2(conn: sqlite3.Connection) -> None:
    """Recreate books_au so only FTS-indexed column changes rewrite the FTS row."""
    conn.executescript(_MIGRATION_TRIGGER_SQL)


def _migrate_v3(conn: sqlite3.Connection) -> None:
    """Drop dead embedding columns (vectors live in data/bge_m3_vectors.npy; these were never written)."""
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(books)").fetchall()}
    for col in ("embedding_blob", "hyde_embedding_blob"):
        if col in existing:
            conn.execute(f"ALTER TABLE books DROP COLUMN {col}")


def _migrate_v4(conn: sqlite3.Connection) -> None:
    """Create book_external_meta table and indexes for multi-source metadata."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS book_external_meta (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            source TEXT NOT NULL,
            external_id TEXT DEFAULT '',
            rating REAL DEFAULT 0.0,
            raw_rating REAL DEFAULT 0.0,
            votes INTEGER DEFAULT 0,
            description TEXT DEFAULT '',
            reviews_json TEXT DEFAULT '[]',
            status TEXT DEFAULT 'success',
            fetched_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(book_id, source)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ext_meta_book ON book_external_meta(book_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ext_meta_source ON book_external_meta(source)")


def _migrate_v5(conn: sqlite3.Connection) -> None:
    """Soft deletion column for removed books."""
    existing_cols = {r["name"] for r in conn.execute("PRAGMA table_info(books)").fetchall()}
    if "is_deleted" not in existing_cols:
        conn.execute("ALTER TABLE books ADD COLUMN is_deleted INTEGER DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_books_is_deleted ON books(is_deleted)")


def _migrate_v6(conn: sqlite3.Connection) -> None:
    """Per-file metadata read from inside the file (OPF / EXTH / PDF info): the signal for telling whether two
    files of one record are the same work, which file names alone get wrong."""
    existing_cols = {r["name"] for r in conn.execute("PRAGMA table_info(book_files)").fetchall()}
    for col in ("embedded_title", "embedded_author", "isbn"):
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE book_files ADD COLUMN {col} TEXT DEFAULT ''")


def _migrate_v7(conn: sqlite3.Connection) -> None:
    """Lets the classics of one category be read best first without sorting it (exploration picks)."""
    conn.execute("CREATE INDEX IF NOT EXISTS idx_books_category_classic ON books(category, classic_score DESC)")


def _migrate_v8(conn: sqlite3.Connection) -> None:
    """When the reader last marked a book (favourite, reading status, rating). updated_at cannot serve: metadata
    refreshes move it. A book marked before this column existed takes the time it was last opened, the nearest
    thing on record; one never opened stays without."""
    existing_cols = {r["name"] for r in conn.execute("PRAGMA table_info(books)").fetchall()}
    if "marked_at" not in existing_cols:
        conn.execute("ALTER TABLE books ADD COLUMN marked_at TEXT")
    # What "marked" means is spelled out here, not imported: a migration must keep doing what it did when it ran
    conn.execute(
        "UPDATE books SET marked_at = last_opened_at "
        "WHERE marked_at IS NULL AND COALESCE(last_opened_at, '') != '' AND (is_favorite = 1 "
        "OR COALESCE(reading_status, 'unread') NOT IN ('unread', '') OR COALESCE(user_rating, 0) > 0)")


def _migrate_v9(conn: sqlite3.Connection) -> None:
    """Consolidate ai_usage, ai_rerank_cache, and title_repair into schema migrations."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_usage (
            day TEXT,
            feature TEXT,
            calls INTEGER DEFAULT 0,
            tokens_in INTEGER DEFAULT 0,
            tokens_out INTEGER DEFAULT 0,
            cost REAL DEFAULT 0,
            PRIMARY KEY (day, feature)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_rerank_cache (
            key TEXT PRIMARY KEY,
            scores TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS title_repair (
            book_id INTEGER PRIMARY KEY,
            old_title TEXT,
            en_title TEXT,
            en_source TEXT,
            zh_title TEXT,
            status TEXT DEFAULT 'planned'
        )
        """
    )


def _migrate_v10(conn: sqlite3.Connection) -> None:
    """Books the reader swiped away on the tonight page, kept as a record of what they did not want."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS book_skips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            context TEXT NOT NULL,
            query TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_book_skips_book ON book_skips(book_id, created_at)")


def _migrate_v11(conn: sqlite3.Connection) -> None:
    """What the reader searched for, newest id last, shown back as search suggestions on phone and desktop."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS search_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL UNIQUE,
            used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _migrate_v12(conn: sqlite3.Connection) -> None:
    """What the reader wanted from each tonight search, and where they asked to see a search afresh."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS search_wants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            query TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_search_wants_query ON search_wants(query)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS search_resets (
            query TEXT PRIMARY KEY,
            after_skip_id INTEGER NOT NULL
        )
        """
    )


_MIGRATIONS = [
    (1, _migrate_v1),
    (2, _migrate_v2),
    (3, _migrate_v3),
    (4, _migrate_v4),
    (5, _migrate_v5),
    (6, _migrate_v6),
    (7, _migrate_v7),
    (8, _migrate_v8),
    (9, _migrate_v9),
    (10, _migrate_v10),
    (11, _migrate_v11),
    (12, _migrate_v12),
]


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Run all pending schema migrations up to the latest target version."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for target, apply in _MIGRATIONS:
        if version < target:
            apply(conn)
            conn.execute(f"PRAGMA user_version = {target}")
            version = target
