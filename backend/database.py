import logging
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set
from config import DB_PATH
from migrations import apply_migrations

_db_lock = threading.RLock()
_wal_lock = threading.Lock()
_wal_initialized: Set[str] = set()


def _configure_conn(conn: sqlite3.Connection, db_path: Path) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    key = str(Path(db_path).resolve())
    if key not in _wal_initialized:
        with _wal_lock:
            if key not in _wal_initialized:
                conn.execute("PRAGMA journal_mode=WAL;")
                _wal_initialized.add(key)
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA cache_size=-20000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def get_conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30.0, check_same_thread=False)
    return _configure_conn(conn, db_path)


@contextmanager
def write_tx(db_path: Path = DB_PATH, conn_factory: Any = None) -> Iterator[sqlite3.Connection]:
    """Open a serialized IMMEDIATE write transaction and commit or roll back cleanly."""
    if conn_factory is not None:
        conn = conn_factory()
        conn.isolation_level = None
    else:
        conn = sqlite3.connect(str(db_path), timeout=30.0, check_same_thread=False, isolation_level=None)
        _configure_conn(conn, db_path)
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path = DB_PATH) -> None:
    with _db_lock:
        conn = get_conn(db_path)
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS books (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dedup_key TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    subtitle TEXT DEFAULT '',
                    norm_title TEXT NOT NULL,
                    author TEXT NOT NULL DEFAULT '未知作者',
                    norm_author TEXT NOT NULL DEFAULT '未知作者',
                    translator TEXT DEFAULT '',
                    publisher TEXT DEFAULT '',
                    pub_year TEXT DEFAULT '',
                    isbn TEXT DEFAULT '',
                    douban_id TEXT DEFAULT '',
                    series TEXT DEFAULT '',
                    volume_part TEXT DEFAULT '',
                    edition_tag TEXT DEFAULT '',
                    category TEXT DEFAULT '未分类',
                    tags TEXT DEFAULT '[]',
                    language TEXT DEFAULT 'zh-CN',
                    page_count INTEGER DEFAULT 0,
                    primary_format TEXT DEFAULT '',
                    primary_path TEXT DEFAULT '',
                    archived_path TEXT DEFAULT '',
                    file_size INTEGER DEFAULT 0,
                    file_hash TEXT DEFAULT '',
                    source_dir TEXT DEFAULT '',
                    cover_path TEXT DEFAULT '',
                    summary TEXT DEFAULT '',
                    summary_source TEXT DEFAULT '',
                    core_points TEXT DEFAULT '[]',
                    target_audience TEXT DEFAULT '',
                    hyde_queries TEXT DEFAULT '[]',
                    toc_text TEXT DEFAULT '',
                    quality_score REAL DEFAULT 0.0,
                    classic_score REAL DEFAULT 0.0,
                    douban_rating REAL DEFAULT 0.0,
                    douban_votes INTEGER DEFAULT 0,
                    is_anthology INTEGER DEFAULT 0,
                    is_deleted INTEGER DEFAULT 0,
                    user_rating INTEGER DEFAULT 0,
                    reading_status TEXT DEFAULT 'unread',
                    is_favorite INTEGER DEFAULT 0,
                    user_notes TEXT DEFAULT '',
                    pinyin_search TEXT DEFAULT '',
                    index_status TEXT DEFAULT 'PENDING',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_books_norm_title ON books(norm_title);
                CREATE INDEX IF NOT EXISTS idx_books_norm_author ON books(norm_author);
                CREATE INDEX IF NOT EXISTS idx_books_category ON books(category);
                CREATE INDEX IF NOT EXISTS idx_books_language ON books(language);
                CREATE INDEX IF NOT EXISTS idx_books_format ON books(primary_format);
                CREATE INDEX IF NOT EXISTS idx_books_source_dir ON books(source_dir);
                CREATE INDEX IF NOT EXISTS idx_books_favorite ON books(is_favorite);
                CREATE INDEX IF NOT EXISTS idx_books_reading_status ON books(reading_status);
                CREATE INDEX IF NOT EXISTS idx_books_quality ON books(quality_score DESC);

                CREATE VIRTUAL TABLE IF NOT EXISTS books_fts USING fts5(
                    title,
                    subtitle,
                    author,
                    translator,
                    series,
                    category,
                    tags,
                    summary,
                    core_points,
                    hyde_queries,
                    toc_text,
                    pinyin_search,
                    isbn,
                    content='books',
                    content_rowid='id',
                    tokenize='unicode61'
                );

                CREATE TRIGGER IF NOT EXISTS books_ai AFTER INSERT ON books BEGIN
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

                CREATE TRIGGER IF NOT EXISTS books_ad AFTER DELETE ON books BEGIN
                    INSERT INTO books_fts(
                        books_fts, rowid, title, subtitle, author, translator, series,
                        category, tags, summary, core_points, hyde_queries,
                        toc_text, pinyin_search, isbn
                    ) VALUES (
                        'delete', old.id, old.title, old.subtitle, old.author, old.translator, old.series,
                        old.category, old.tags, old.summary, old.core_points, old.hyde_queries,
                        old.toc_text, old.pinyin_search, old.isbn
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS books_au AFTER UPDATE ON books
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

                CREATE TABLE IF NOT EXISTS book_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                    original_path TEXT UNIQUE NOT NULL,
                    archived_path TEXT DEFAULT '',
                    format TEXT NOT NULL,
                    file_size INTEGER DEFAULT 0,
                    file_hash TEXT DEFAULT '',
                    source_name TEXT DEFAULT '',
                    is_primary INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_book_files_book_id ON book_files(book_id);
                CREATE INDEX IF NOT EXISTS idx_book_files_hash ON book_files(file_hash);

                CREATE TABLE IF NOT EXISTS conflict_inbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT UNIQUE NOT NULL,
                    source_name TEXT DEFAULT '',
                    format TEXT DEFAULT '',
                    file_size INTEGER DEFAULT 0,
                    detected_title TEXT DEFAULT '',
                    detected_author TEXT DEFAULT '',
                    conflict_type TEXT NOT NULL,
                    conflict_detail TEXT DEFAULT '',
                    candidate_book_id INTEGER DEFAULT NULL,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_conflict_status ON conflict_inbox(status);

                CREATE TABLE IF NOT EXISTS user_interest_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    interest_prompt TEXT DEFAULT '深入理解计算机系统架构、人工智能与算法、认知心理学与行为决策、历史政治博弈、经典科幻与哲学思考',
                    preferred_categories TEXT DEFAULT '["计算机与AI", "心理与认知", "历史与传记", "经济与商业", "科幻与奇幻", "哲学与社科"]',
                    excluded_categories TEXT DEFAULT '[]',
                    exploration_ratio REAL DEFAULT 0.2,
                    schedule_enabled INTEGER DEFAULT 1,
                    schedule_interval_hours INTEGER DEFAULT 24,
                    last_scheduled_at TEXT DEFAULT '',
                    llm_api_base TEXT DEFAULT '',
                    llm_api_key TEXT DEFAULT '',
                    llm_model TEXT DEFAULT '',
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                INSERT OR IGNORE INTO user_interest_config (id) VALUES (1);

                CREATE TABLE IF NOT EXISTS organize_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_type TEXT DEFAULT 'manual',
                    status TEXT DEFAULT 'running',
                    scanned_files INTEGER DEFAULT 0,
                    indexed_books INTEGER DEFAULT 0,
                    archived_links INTEGER DEFAULT 0,
                    duplicates_merged INTEGER DEFAULT 0,
                    conflicts_found INTEGER DEFAULT 0,
                    ocr_triggered INTEGER DEFAULT 0,
                    summary_generated INTEGER DEFAULT 0,
                    detail_json TEXT DEFAULT '{}',
                    started_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    finished_at TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS file_ops_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER DEFAULT NULL,
                    op_type TEXT NOT NULL,
                    src_path TEXT NOT NULL,
                    dst_path TEXT NOT NULL,
                    book_id INTEGER DEFAULT NULL,
                    rolled_back INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_file_ops_run_id ON file_ops_log(run_id);

                CREATE TABLE IF NOT EXISTS online_metadata_cache (
                    query_key TEXT PRIMARY KEY,
                    response_json TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS translation_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text_hash TEXT UNIQUE,
                    source_lang TEXT,
                    target_lang TEXT,
                    original_text TEXT,
                    translated_text TEXT,
                    phonetic TEXT,
                    dict_json TEXT,
                    engine TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_trans_hash ON translation_cache(text_hash);

                -- DeepSeek-derived metadata (backend/enrich_ai.py); books' own columns are never overwritten
                CREATE TABLE IF NOT EXISTS book_ai_meta (
                    book_id INTEGER PRIMARY KEY,
                    clean_title TEXT DEFAULT '',
                    keywords TEXT DEFAULT '[]',
                    level TEXT DEFAULT '',
                    kind TEXT DEFAULT '',
                    ai_category TEXT DEFAULT '',
                    subfield TEXT DEFAULT '',
                    input_hash TEXT DEFAULT '',
                    model TEXT DEFAULT '',
                    version INTEGER DEFAULT 1,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

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
                );
                -- Cards show clean_title: any title write replaces it in the same statement, so card and detail agree
                CREATE TRIGGER IF NOT EXISTS books_title_resets_clean_title AFTER UPDATE OF title ON books
                WHEN old.title IS NOT new.title
                BEGIN
                    UPDATE book_ai_meta SET clean_title = new.title WHERE book_id = new.id;
                END;
                CREATE INDEX IF NOT EXISTS idx_ext_meta_book ON book_external_meta(book_id);
                CREATE INDEX IF NOT EXISTS idx_ext_meta_source ON book_external_meta(source);
                """
            )
            apply_migrations(conn)
            conn.commit()
        finally:
            conn.close()


def backup_database(keep: int = 7) -> Optional[Path]:
    """Consistent snapshot of the live DB (SQLite backup API) into BASE_DIR/backups; keeps the newest `keep`."""
    dest_dir = DB_PATH.parent.parent / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"library-{time.strftime('%Y%m%d-%H%M%S')}.db"
    src = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    backups = sorted(dest_dir.glob("library-*.db"), key=lambda p: p.stat().st_mtime)
    for old in backups[:-keep]:
        try:
            old.unlink()
        except OSError:
            # Stale backup cleanup failure should not prevent current backup success
            logging.getLogger(__name__).warning("could not delete an old database backup", exc_info=True)
    return dest


def upsert_external_meta(db_path: Path, meta_dict: Optional[Dict[str, Any]] = None, **kwargs) -> None:
    """Upsert book external metadata record from an external channel."""
    data = dict(meta_dict or {})
    data.update(kwargs)
    book_id = int(data["book_id"])
    source = str(data["source"]).strip()
    ext_id = str(data.get("external_id", "")).strip()
    rating = float(data.get("rating", 0.0))
    raw_rating = float(data.get("raw_rating", 0.0))
    votes = int(data.get("votes", 0))
    desc = str(data.get("description", "")).strip()
    reviews = data.get("reviews", [])
    reviews_json = json.dumps(reviews, ensure_ascii=False) if isinstance(reviews, list) else str(reviews)
    status = str(data.get("status", "success"))

    conn = get_conn(db_path)
    try:
        conn.execute(
            """
            INSERT INTO book_external_meta (
                book_id, source, external_id, rating, raw_rating, votes,
                description, reviews_json, status, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(book_id, source) DO UPDATE SET
                external_id = excluded.external_id,
                rating = excluded.rating,
                raw_rating = excluded.raw_rating,
                votes = excluded.votes,
                description = excluded.description,
                reviews_json = excluded.reviews_json,
                status = excluded.status,
                fetched_at = CURRENT_TIMESTAMP
            """,
            (book_id, source, ext_id, rating, raw_rating, votes, desc, reviews_json, status),
        )
        conn.commit()
    finally:
        conn.close()


def get_external_meta_by_book(db_path: Path, book_id: int) -> List[Dict[str, Any]]:
    """Retrieve all external metadata records for a given book."""
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM book_external_meta WHERE book_id = ? ORDER BY id ASC",
            (book_id,),
        ).fetchall()
        result = []
        for r in rows:
            item = dict(r)
            try:
                item["reviews"] = json.loads(item.get("reviews_json") or "[]")
            except Exception:
                item["reviews"] = []
            result.append(item)
        return result
    finally:
        conn.close()

