"""
Undo one split run of mismerge_service: files go back to their original records exactly as they were, the
records the run created are removed, and Library links are restored before the run's own links are dropped.

Anything done to the created records after the split (favourites, notes) is lost with them.
"""

import json
import logging
import os
from typing import Any, Dict, List

from config import LIBRARY_DIR
from database import get_conn

logger = logging.getLogger("split_undo")

_RELATED_TABLES = ("book_ai_meta", "book_external_meta")


def _split_ops(conn, run_id: int) -> List[Any]:
    return conn.execute(
        "SELECT * FROM file_ops_log WHERE run_id = ? AND op_type LIKE 'split%' AND rolled_back = 0 ORDER BY id",
        (run_id,),
    ).fetchall()


def _restore_links(ops: List[Any]) -> int:
    """Re-create links a split superseded, from the new link (the same file), before the new links go."""
    restored = 0
    for op in ops:
        old, new = op["src_path"], op["dst_path"]
        if op["op_type"] == "split_unlink" and not os.path.exists(old) and new and os.path.exists(new):
            os.makedirs(os.path.dirname(old), exist_ok=True)
            os.link(new, old)
            restored += 1
    return restored


def _restore_origin(conn, op: Any) -> None:
    state = json.loads(op["src_path"])
    book = state["book"]
    assignments = ", ".join(f"{column} = ?" for column in book)
    conn.execute(f"UPDATE books SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                 (*book.values(), op["book_id"]))
    for file_id, is_primary, archived_path in state["files"]:
        conn.execute("UPDATE book_files SET book_id = ?, is_primary = ?, archived_path = ? WHERE id = ?",
                     (op["book_id"], is_primary, archived_path, file_id))


def _delete_created_books(conn, book_ids: List[int]) -> None:
    for book_id in book_ids:
        leftover = conn.execute("SELECT COUNT(*) FROM book_files WHERE book_id = ?", (book_id,)).fetchone()[0]
        if leftover:  # every file of a created record came from an origin snapshot; refuse to orphan others
            raise RuntimeError(f"book #{book_id} still holds {leftover} files after restoring; undo aborted")
        for table in _RELATED_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE book_id = ?", (book_id,))
        conn.execute("DELETE FROM books WHERE id = ?", (book_id,))


def _drop_run_links(conn, ops: List[Any]) -> int:
    """Links the run created for new records or promoted anchors, once nothing refers to them."""
    dropped = 0
    for op in ops:
        link = op["dst_path"] if op["op_type"] in ("split_new_book", "split_link") else ""
        if not (link and link.startswith(str(LIBRARY_DIR)) and os.path.exists(link)):
            continue
        referenced = conn.execute("SELECT 1 FROM books WHERE archived_path = ? UNION SELECT 1 FROM book_files "
                                  "WHERE archived_path = ?", (link, link)).fetchone()
        if not referenced and os.stat(link).st_nlink > 1:  # never the last copy of a file
            os.unlink(link)
            dropped += 1
    return dropped


def undo_split_run(run_id: int) -> Dict[str, Any]:
    """Reverse every split of `run_id`. Raises when the run has nothing left to undo."""
    from http_api.background import spawn_reindex

    conn = get_conn()
    try:
        ops = _split_ops(conn, run_id)
        if not ops:
            raise ValueError(f"run {run_id} has no split to undo")
        restored_links = _restore_links(ops)
        origins = [op for op in ops if op["op_type"] == "split_origin"]
        created = [op["book_id"] for op in ops if op["op_type"] == "split_new_book"]
        for op in origins:
            _restore_origin(conn, op)
        _delete_created_books(conn, created)
        conn.execute("UPDATE file_ops_log SET rolled_back = 1 WHERE run_id = ? AND op_type LIKE 'split%'", (run_id,))
        conn.execute("UPDATE organize_runs SET status = 'rolled_back' WHERE id = ?", (run_id,))
        conn.commit()
        dropped_links = _drop_run_links(conn, ops)
    finally:
        conn.close()
    origin_ids = [op["book_id"] for op in origins]
    spawn_reindex(origin_ids + created)
    logger.info("Undid split run %s: %s records restored, %s removed", run_id, len(origin_ids), len(created))
    return {"ok": True, "run_id": run_id, "restored_books": origin_ids, "removed_books": created,
            "restored_links": restored_links, "dropped_links": dropped_links}
