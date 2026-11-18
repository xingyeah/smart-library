"""Service for soft-deleting books and relocating their physical files to a dedicated storage directory."""

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from config import LIBRARY_DIR, REMOVED_BOOKS_DIR, is_path_within
from database import get_conn, write_tx
from http_api.background import spawn_reindex
from same_work import DIFFERENT, UNCERTAIN, plan_book_split

logger = logging.getLogger("book_removal")


class BookNotFoundError(Exception):
    """Raised when book ID does not exist in library database."""


class BookAlreadyDeletedError(Exception):
    """Raised when book is already marked as deleted."""


class DeleteNeedsConfirmation(Exception):
    """A book with several files is deleted only after the caller confirms that exact file list.

    One record can hold files of different works (a mis-merge moved 5 unrelated books on delete), so the caller
    is shown every file, with the ones that look like another book flagged, before anything is moved.
    """

    def __init__(self, files: List[Dict[str, Any]]):
        super().__init__(f"{len(files)} files need confirmation")
        self.files = files


def describe_book_files(book: Any, file_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Each file with a flag for files the same-work rules place in another book (or cannot place)."""
    plan = plan_book_split(file_rows, book["title"]) if len(file_rows) > 1 else {DIFFERENT: [], UNCERTAIN: []}
    flagged = set(plan[DIFFERENT]) | set(plan[UNCERTAIN])
    return [
        {
            "id": int(r["id"]),
            "name": os.path.basename(r.get("original_path") or r.get("archived_path") or ""),
            "format": r.get("format") or "",
            "looks_like_other_book": int(r["id"]) in flagged,
        }
        for r in file_rows
    ]


def _sanitize_folder_name(title: str, max_length: int = 50) -> str:
    """Produce safe directory component from book title."""
    clean = re.sub(r'[\\/*?:"<>|#&%\s]+', "_", title.strip())
    clean = clean.strip("._") or "untitled"
    return clean[:max_length]


def _cleanup_empty_parent_dirs(path_str: str, stop_dir: Path) -> None:
    """Recursively remove empty parent folders up to stop_dir."""
    try:
        curr = Path(path_str).parent
        while curr != stop_dir and curr.exists() and not any(curr.iterdir()):
            curr.rmdir()
            curr = curr.parent
    except OSError as e:
        logger.debug("Failed pruning empty parent directory %s: %s", path_str, e)


def _remove_library_hardlink(link_path: str) -> bool:
    """Unlink a normalized Library hardlink and prune empty author directory."""
    if not (link_path and os.path.exists(link_path)):
        return False
    if not is_path_within(link_path, LIBRARY_DIR):
        return False
    try:
        os.unlink(link_path)
        _cleanup_empty_parent_dirs(link_path, LIBRARY_DIR)
        return True
    except OSError as e:
        logger.warning("Failed unlinking library file %s: %s", link_path, e)
        return False


def _unique_dest_path(dest_dir: Path, filename: str) -> Path:
    """Generate conflict-free file path within target destination."""
    dest_path = dest_dir / filename
    if not dest_path.exists():
        return dest_path
    stem, ext = os.path.splitext(filename)
    counter = 1
    while dest_path.exists():
        dest_path = dest_dir / f"{stem}_{counter}{ext}"
        counter += 1
    return dest_path


def _relocate_single_file(src_path: str, dest_dir: Path) -> Optional[str]:
    """Move physical source file across filesystems/disks to destination folder."""
    if not (src_path and os.path.exists(src_path)):
        return None
    dest_path = _unique_dest_path(dest_dir, os.path.basename(src_path))
    try:
        shutil.move(src_path, dest_path)
        return str(dest_path)
    except Exception as e:
        logger.error("Failed moving file %s to %s: %s", src_path, dest_path, e)
        return None


def _collect_book_file_targets(conn, book_id: int) -> Tuple[List[Dict[str, Any]], Set[str]]:
    """Gather primary and secondary file records plus all distinct hardlinks."""
    file_rows = [
        dict(r)
        for r in conn.execute("SELECT * FROM book_files WHERE book_id = ?", (book_id,)).fetchall()
    ]
    hardlinks = set()
    for row in file_rows:
        archived = (row.get("archived_path") or "").strip()
        if archived:
            hardlinks.add(archived)
    return file_rows, hardlinks


def _process_file_relocation(
    file_rows: List[Dict[str, Any]], dest_dir: Path
) -> Tuple[List[str], Dict[int, str]]:
    """Move original files to destination and record new mapping."""
    moved_paths: List[str] = []
    file_id_to_new_path: Dict[int, str] = {}
    seen_src: Set[str] = set()

    for r in file_rows:
        fid = r["id"]
        orig = (r.get("original_path") or "").strip()
        if not orig or orig in seen_src:
            continue
        seen_src.add(orig)
        new_path = _relocate_single_file(orig, dest_dir)
        if new_path:
            moved_paths.append(new_path)
            file_id_to_new_path[fid] = new_path

    return moved_paths, file_id_to_new_path


def _persist_removal_records(
    conn, book_id: int, moved_paths: List[str], file_id_map: Dict[int, str]
) -> None:
    """Mark book as deleted and update file paths in database."""
    primary_new = moved_paths[0] if moved_paths else ""
    conn.execute(
        """
        UPDATE books SET
            is_deleted = 1,
            archived_path = '',
            primary_path = CASE WHEN ? != '' THEN ? ELSE primary_path END,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (primary_new, primary_new, book_id),
    )
    for fid, new_path in file_id_map.items():
        conn.execute(
            "UPDATE book_files SET original_path = ?, archived_path = '' WHERE id = ?",
            (new_path, fid),
        )
    conn.execute(
        "INSERT INTO file_ops_log (op_type, src_path, dst_path, book_id) VALUES (?, ?, ?, ?)",
        ("remove_book", f"book_id:{book_id}", primary_new, book_id),
    )


def _rollback_moved_files(file_rows: List[Dict[str, Any]], file_id_map: Dict[int, str], book_id: int) -> None:
    orig_by_id = {r["id"]: (r.get("original_path") or "").strip() for r in file_rows}
    for fid, new_path in file_id_map.items():
        orig = orig_by_id.get(fid, "")
        if orig and os.path.exists(new_path) and not os.path.exists(orig):
            try:
                shutil.move(new_path, orig)
            except OSError as exc:
                logger.warning("Rollback move failed %s -> %s: %s", new_path, orig, exc)
    with write_tx(conn_factory=get_conn) as conn:
        conn.execute("UPDATE books SET is_deleted = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (book_id,))


def remove_book_and_relocate_files(book_id: int, confirmed_file_ids: Optional[List[int]] = None) -> Dict[str, Any]:
    """Safely archive all files for book into removed_books folder and mark as soft-deleted."""
    conn = get_conn()
    try:
        book = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        if not book:
            raise BookNotFoundError(f"Book #{book_id} not found")
        if int(book["is_deleted"] or 0) == 1:
            raise BookAlreadyDeletedError(f"Book #{book_id} is already deleted")
        title = book["title"]
        file_rows, hardlinks = _collect_book_file_targets(conn, book_id)
        if len(file_rows) > 1 and set(confirmed_file_ids or []) != {int(r["id"]) for r in file_rows}:
            raise DeleteNeedsConfirmation(describe_book_files(book, file_rows))
        if book["archived_path"]:
            hardlinks.add(book["archived_path"])
    finally:
        conn.close()

    with write_tx(conn_factory=get_conn) as conn:
        conn.execute("UPDATE books SET is_deleted = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (book_id,))

    removed_links = [hl for hl in hardlinks if _remove_library_hardlink(hl)]
    dest_dir = REMOVED_BOOKS_DIR / f"{book_id}_{_sanitize_folder_name(title)}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    moved_paths, file_id_map = _process_file_relocation(file_rows, dest_dir)
    try:
        with write_tx(conn_factory=get_conn) as conn:
            _persist_removal_records(conn, book_id, moved_paths, file_id_map)
    except Exception:
        _rollback_moved_files(file_rows, file_id_map, book_id)
        raise

    spawn_reindex({book_id})
    logger.info("Successfully soft-deleted book #%s (%s), moved %s files", book_id, title, len(moved_paths))
    return {
        "ok": True, "book_id": book_id, "title": title,
        "moved_files": moved_paths, "removed_links": removed_links, "dest_dir": str(dest_dir),
    }
