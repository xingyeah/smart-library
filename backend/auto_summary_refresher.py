"""Automatic background AI summary refresher for prioritized books.

Monitors books that are favorited, marked 'want'/'reading', or have external/Douban ratings,
and queues `refresh_book_ai_metadata(..., feature='auto_refresh')` when their Chinese synopsis
or structured AI fields are missing/suspicious.
"""

import logging
import queue
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from config import DB_PATH
from database import get_conn
from deepseek_client import DailyCapReached
from jobs import spawn_reindex

logger = logging.getLogger("auto_summary_refresher")

UNREADABLE_MODEL_MARKER = "no_readable_text"
MIN_VALID_SUMMARY_CHARS = 35
REINDEX_BATCH_SIZE = 10
SWEEP_IDLE_WAIT_SEC = 60.0
CAP_COOLDOWN_SEC = 600.0
ITEM_COOLDOWN_SEC = 2.0
FAILURE_BACKOFF_SEC = 1800.0

_FRONTMATTER_NOISE = ("isbn", "copyright", "all rights reserved", "table of contents", "printed in")

_queue: "queue.Queue[int]" = queue.Queue()
_queued_lock = threading.Lock()
_queued_ids: Set[int] = set()
_backoff_lock = threading.Lock()
_failed_until: Dict[int, float] = {}
_worker_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()


def _is_in_backoff(book_id: int) -> bool:
    now = time.time()
    with _backoff_lock:
        until = _failed_until.get(book_id, 0.0)
        if until > now:
            return True
        _failed_until.pop(book_id, None)
        return False


def _record_failure_backoff(book_id: int) -> None:
    with _backoff_lock:
        _failed_until[book_id] = time.time() + FAILURE_BACKOFF_SEC


def is_summary_valid_chinese(summary: str) -> bool:
    """Return True when summary looks like a clean Chinese synopsis rather than raw blurb/noise."""
    text = (summary or "").strip()
    if len(text) < MIN_VALID_SUMMARY_CHARS:
        return False
    if text.startswith("针对《"):
        return False
    lowered = text.lower()
    if any(tok in lowered for tok in _FRONTMATTER_NOISE):
        return False
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin_count = len(re.findall(r"[A-Za-z]", text))
    if cjk_count == 0 and latin_count > 20:
        return False
    if latin_count > cjk_count * 1.5 and cjk_count < 40:
        return False
    return True


def is_book_in_priority_pool(conn: Any, book_id: int) -> bool:
    """Return True if book is active and favorited, want/reading, or has any positive rating."""
    row = conn.execute(
        """
        SELECT 1 FROM books b
        WHERE b.id = ? AND b.is_deleted = 0 AND (
            b.is_favorite = 1
            OR b.reading_status IN ('want', 'reading')
            OR b.douban_rating > 0
            OR EXISTS (SELECT 1 FROM book_external_meta em WHERE em.book_id = b.id AND em.rating > 0)
        )
        """,
        (book_id,),
    ).fetchone()
    return row is not None


def needs_ai_summary_refresh(conn: Any, book_id: int) -> bool:
    """Return True when a priority book still lacks valid AI-refined summary/metadata."""
    row = conn.execute(
        """
        SELECT b.summary, b.summary_source, b.target_audience, b.core_points,
               m.book_id AS meta_book_id, m.model AS meta_model
        FROM books b
        LEFT JOIN book_ai_meta m ON b.id = m.book_id
        WHERE b.id = ? AND b.is_deleted = 0
        """,
        (book_id,),
    ).fetchone()
    if not row:
        return False
    if (row["meta_model"] or "") == UNREADABLE_MODEL_MARKER:
        return False
    if (row["summary_source"] or "") != "ai_refreshed":
        return True
    if row["meta_book_id"] is None:
        return True
    if not is_summary_valid_chinese(row["summary"] or ""):
        return True
    if not (row["target_audience"] or "").strip():
        return True
    if (row["core_points"] or "").strip() in ("", "[]"):
        return True
    return False


def mark_unreadable_for_auto_refresh(book_id: int, db_path: Path = DB_PATH) -> None:
    """Persist skip marker so scan-only books without text are not retried automatically."""
    conn = get_conn(db_path)
    try:
        conn.execute(
            """
            INSERT INTO book_ai_meta
                (book_id, clean_title, keywords, level, kind, ai_category, subfield, input_hash, model, version, updated_at)
            VALUES (?, '', '[]', '', '', '', '', '', ?, -1, CURRENT_TIMESTAMP)
            ON CONFLICT(book_id) DO UPDATE SET
                model = excluded.model,
                version = excluded.version,
                updated_at = CURRENT_TIMESTAMP
            """,
            (book_id, UNREADABLE_MODEL_MARKER),
        )
        conn.commit()
    finally:
        conn.close()


def fetch_next_missing_target_book(conn: Any) -> Optional[int]:
    """Pick highest-priority book in rated/favorite/want/reading pool needing AI summary refresh."""
    rows = conn.execute(
        """
        SELECT b.id, b.summary, b.summary_source, b.target_audience, b.core_points,
               m.book_id AS meta_book_id, m.model AS meta_model
        FROM books b
        LEFT JOIN book_ai_meta m ON b.id = m.book_id
        WHERE b.is_deleted = 0
          AND COALESCE(m.model, '') != ?
          AND (
              b.is_favorite = 1
              OR b.reading_status IN ('want', 'reading')
              OR b.douban_rating > 0
              OR EXISTS (SELECT 1 FROM book_external_meta em WHERE em.book_id = b.id AND em.rating > 0)
          )
          AND (
              m.book_id IS NULL
              OR COALESCE(b.summary_source, '') != 'ai_refreshed'
              OR COALESCE(TRIM(b.target_audience), '') = ''
              OR COALESCE(TRIM(b.core_points), '') IN ('', '[]')
              OR LENGTH(TRIM(COALESCE(b.summary, ''))) < ?
              OR COALESCE(b.summary, '') NOT GLOB '*[一-龥]*'
          )
        ORDER BY b.is_favorite DESC,
                 CASE b.reading_status WHEN 'reading' THEN 0 WHEN 'want' THEN 1 ELSE 2 END,
                 b.id ASC
        LIMIT 20
        """,
        (UNREADABLE_MODEL_MARKER, MIN_VALID_SUMMARY_CHARS),
    ).fetchall()
    for r in rows:
        bid = int(r["id"])
        if _is_in_backoff(bid):
            continue
        if (
            r["meta_book_id"] is None
            or (r["summary_source"] or "") != "ai_refreshed"
            or not is_summary_valid_chinese(r["summary"] or "")
            or not (r["target_audience"] or "").strip()
            or (r["core_points"] or "").strip() in ("", "[]")
        ):
            return bid
    return None


def enqueue_book_summary_refresh(book_id: int) -> bool:
    """Queue a book for background AI summary refinement (deduplicated, non-blocking)."""
    bid = int(book_id)
    with _queued_lock:
        if bid in _queued_ids:
            return False
        _queued_ids.add(bid)
    _queue.put(bid)
    return True


def _process_candidate(
    book_id: int, db_path: Path, pending_reindex: Optional[List[int]] = None
) -> bool:
    """Run auto-refresh for one book if eligible; returns False when daily cap is reached."""
    import book_enrichment_service as bes

    conn = get_conn(db_path)
    try:
        if not (is_book_in_priority_pool(conn, book_id) and needs_ai_summary_refresh(conn, book_id)):
            return True
    finally:
        conn.close()

    try:
        cb = pending_reindex.append if pending_reindex is not None else None
        bes.refresh_book_ai_metadata(book_id, feature="auto_refresh", reindex_fn=cb)
        logger.info("Auto-refreshed AI summary for book #%s", book_id)
    except bes.NoReadableTextError:
        mark_unreadable_for_auto_refresh(book_id, db_path=db_path)
        logger.info("Marked book #%s unreadable for auto-refresh", book_id)
    except bes.BookNotFoundError:
        logger.debug("Book #%s missing before auto-refresh; skipping", book_id)
    except DailyCapReached as e:
        logger.info("Auto summary refresh paused: %s", e)
        return False
    except Exception as e:
        _record_failure_backoff(book_id)
        logger.warning("Auto summary refresh failed for book #%s (backoff %.0fs): %s", book_id, FAILURE_BACKOFF_SEC, e)
    return True


def _flush_pending_reindex(pending_reindex: List[int]) -> None:
    if not pending_reindex:
        return

    batch = list(dict.fromkeys(pending_reindex))
    pending_reindex.clear()
    spawn_reindex(batch)


def _refresher_loop(db_path: Path) -> None:
    _stop_event.wait(20.0)
    pending_reindex: List[int] = []
    while not _stop_event.is_set():
        bid: Optional[int] = None
        try:
            bid = _queue.get_nowait()
        except queue.Empty:
            conn = get_conn(db_path)
            try:
                bid = fetch_next_missing_target_book(conn)
            finally:
                conn.close()

        if bid is None:
            _flush_pending_reindex(pending_reindex)
            _stop_event.wait(SWEEP_IDLE_WAIT_SEC)
            continue

        try:
            ok = _process_candidate(bid, db_path, pending_reindex=pending_reindex)
        finally:
            with _queued_lock:
                _queued_ids.discard(bid)

        if len(pending_reindex) >= REINDEX_BATCH_SIZE or not ok:
            _flush_pending_reindex(pending_reindex)
        _stop_event.wait(ITEM_COOLDOWN_SEC if ok else CAP_COOLDOWN_SEC)
    _flush_pending_reindex(pending_reindex)


def start_background_auto_summary_refresher(db_path: Path = DB_PATH) -> None:
    """Start single-worker background daemon for automatic AI summary refinement."""
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _stop_event.clear()
    _worker_thread = threading.Thread(
        target=_refresher_loop,
        args=(db_path,),
        daemon=True,
        name="AutoSummaryRefresher",
    )
    _worker_thread.start()


def stop_background_auto_summary_refresher() -> None:
    """Signal the auto summary refresher daemon thread to stop."""
    _stop_event.set()
