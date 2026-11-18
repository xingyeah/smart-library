"""
Server-rendered PDF pages for the phone reader.

pdf.js draws every page on the phone's main thread, so scrolling stutters however few pages are kept. The phone
instead shows JPEGs that macOS PDFKit renders here once and caches under data/pdf_pages/<book id>/; the browser
decodes images off the main thread. The cache is kept under CACHE_LIMIT_BYTES by dropping the books read longest ago.
"""

import json
import logging
import os
import queue
import shutil
import threading
from pathlib import Path
from typing import Any, Dict, List

import pdf_text_layout
from config import DATA_DIR
from database import get_conn
from native_bridge import get_native_bridge

logger = logging.getLogger("pdf_page_service")

PAGE_CACHE_DIR = DATA_DIR / "pdf_pages"
PAGE_WIDTH_PX = 1242  # 3x a 414 pt iPhone screen: sharp up to 2x zoom on any phone
JPEG_QUALITY = 0.4  # text pages stay crisp; ~300 KB a page against ~600 KB at 0.8
PREFETCH_PAGES = 5
CACHE_LIMIT_BYTES = 2 * 1024 ** 3
LAYOUT_FILE = "layout.json"

_prefetch_queue: "queue.Queue[tuple]" = queue.Queue()
_prefetch_pending = set()
_prefetch_lock = threading.Lock()
_prefetch_thread = None


class PdfPagesUnavailable(Exception):
    """The book is not a PDF this server can render (missing file, encrypted, or an old native library)."""


def _book_pdf_path(book_id: int) -> str:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT primary_path, archived_path, primary_format FROM books WHERE id = ?", (book_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row or (row["primary_format"] or "").lower() != "pdf":
        raise PdfPagesUnavailable(f"book {book_id} is not a PDF")
    for path in (row["archived_path"], row["primary_path"]):
        if path and os.path.isfile(path) and os.access(path, os.R_OK):
            return path
    raise PdfPagesUnavailable(f"book {book_id}: PDF file not found")


def _source_stamp(pdf_path: str) -> Dict[str, Any]:
    stat = os.stat(pdf_path)
    return {"path": pdf_path, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def page_layout(book_id: int) -> Dict[str, Any]:
    """{"page_count", "sizes": [[w, h], ...] in points}. Wipes the book's cached pages when its file changed."""
    bridge = get_native_bridge()
    if not bridge.can_render_pdf_pages:
        raise PdfPagesUnavailable("native library has no PDF page rendering; rerun start.sh to rebuild it")
    pdf_path = _book_pdf_path(book_id)
    book_dir = PAGE_CACHE_DIR / str(book_id)
    layout_path = book_dir / LAYOUT_FILE
    stamp = _source_stamp(pdf_path)
    if layout_path.exists():
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
        if layout.get("source") == stamp:
            os.utime(book_dir)  # marks the book as recently read for eviction
            return layout
        shutil.rmtree(book_dir)
    sizes = bridge.pdf_page_sizes(pdf_path)
    if not sizes:
        raise PdfPagesUnavailable(f"book {book_id}: PDFKit cannot open {os.path.basename(pdf_path)}")
    layout = {"source": stamp, "page_count": len(sizes), "sizes": sizes}
    book_dir.mkdir(parents=True, exist_ok=True)
    layout_path.write_text(json.dumps(layout), encoding="utf-8")
    return layout


def _check_page(layout: Dict[str, Any], page_no: int) -> None:
    if not 1 <= page_no <= layout["page_count"]:
        raise ValueError(f"page {page_no} outside 1..{layout['page_count']}")


def _render(book_id: int, pdf_path: str, page_no: int) -> Path:
    image_path = PAGE_CACHE_DIR / str(book_id) / f"{page_no}.jpg"
    if image_path.exists():
        return image_path
    if not get_native_bridge().render_pdf_page(pdf_path, page_no - 1, PAGE_WIDTH_PX, JPEG_QUALITY, str(image_path)):
        raise PdfPagesUnavailable(f"book {book_id}: rendering page {page_no} failed")
    return image_path


def page_image(book_id: int, page_no: int) -> Path:
    """JPEG of page `page_no` (1-based), rendered now if not cached; the next pages render in the background."""
    layout = page_layout(book_id)
    _check_page(layout, page_no)
    pdf_path = layout["source"]["path"]
    image_path = _render(book_id, pdf_path, page_no)
    last = min(layout["page_count"], page_no + PREFETCH_PAGES)
    _schedule_prefetch([(book_id, pdf_path, p) for p in range(page_no + 1, last + 1)])
    return image_path


def page_blocks(book_id: int, page_no: int) -> List[Dict[str, Any]]:
    """Blocks of page `page_no` (1-based) for the page-text sheet and side-by-side translation (pdf_text_layout)."""
    layout = page_layout(book_id)
    _check_page(layout, page_no)
    page = get_native_bridge().pdf_page_lines(layout["source"]["path"], page_no - 1)
    if not page.get("ok"):
        raise PdfPagesUnavailable(f"book {book_id}: cannot read the text of page {page_no}")
    return pdf_text_layout.page_blocks(page["lines"], page["width"], page["height"])


def _schedule_prefetch(jobs: List[tuple]) -> None:
    global _prefetch_thread
    with _prefetch_lock:
        for job in jobs:
            if job not in _prefetch_pending:
                _prefetch_pending.add(job)
                _prefetch_queue.put(job)
        if _prefetch_thread is None:
            _prefetch_thread = threading.Thread(target=_prefetch_worker, name="pdf-page-prefetch", daemon=True)
            _prefetch_thread.start()


def _prefetch_worker() -> None:
    while True:
        job = _prefetch_queue.get()
        try:
            _render(*job)
        except Exception as e:  # a failed prefetch only means the page renders on request instead
            logger.warning("Prefetch of book %s page %s failed: %s", job[0], job[2], e)
        finally:
            with _prefetch_lock:
                _prefetch_pending.discard(job)
        if _prefetch_queue.empty():
            _evict_over_limit(keep_book=job[0])


def _evict_over_limit(keep_book: int) -> None:
    """Drop whole books, read longest ago first, until the cache fits CACHE_LIMIT_BYTES."""
    books = []
    for entry in os.scandir(PAGE_CACHE_DIR):
        if entry.is_dir() and entry.name != str(keep_book):
            size = sum(f.stat().st_size for f in os.scandir(entry.path) if f.is_file())
            books.append((entry.stat().st_mtime, size, entry.path))
    keep_dir = PAGE_CACHE_DIR / str(keep_book)
    total = sum(size for _, size, _ in books)
    total += sum(f.stat().st_size for f in os.scandir(keep_dir) if f.is_file()) if keep_dir.exists() else 0
    for _, size, path in sorted(books):
        if total <= CACHE_LIMIT_BYTES:
            break
        shutil.rmtree(path)
        total -= size
        logger.info("Evicted cached PDF pages %s (%d bytes)", path, size)
