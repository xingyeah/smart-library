import hashlib
import json
import logging
import os
import subprocess
import threading
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List

from config import COVERS_DIR, DATA_DIR
from database import get_conn
from http_api.responses import send_json, send_range_stream
from reader import (
    extract_book_reader_content,
    get_epub_asset_bytes,
    get_mobi_image_bytes,
)

logger = logging.getLogger("http_api.reading")

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
PROJECT_DIR = BACKEND_DIR.parent
FRONTEND_DIR = PROJECT_DIR / "frontend"


def handle_get_reader_content(
    handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any], book_id: str
) -> None:
    """GET /api/books/(?P<book_id>\\d+)/read: Extract parsed HTML content and TOC for web reader."""
    bid = int(book_id)
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM books WHERE id = ?", (bid,)).fetchone()
    finally:
        conn.close()

    if not row:
        send_json(handler, {"ok": False, "error": "Not found"}, 404)
        return

    try:
        saved_pos = json.loads(row["read_pos"]) if row["read_pos"] else None
    except ValueError:
        saved_pos = None

    q_chap = (qs.get("chapter") or [""])[0]
    chapter_idx = int(q_chap or (saved_pos or {}).get("chapter_idx") or 0)
    res = extract_book_reader_content(row, chapter_idx=chapter_idx)
    res["saved_pos"] = saved_pos
    send_json(handler, res)


def handle_get_epub_asset(
    handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any], book_id: str
) -> None:
    """GET /api/books/(?P<book_id>\\d+)/epub_asset: Stream embedded images or stylesheets from EPUB archive."""
    bid = int(book_id)
    asset_path = (qs.get("path") or [""])[0]
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT primary_path, archived_path FROM books WHERE id = ?", (bid,)
        ).fetchone()
    finally:
        conn.close()

    if not row or not asset_path:
        handler.send_error(404, "Asset not found")
        return

    res_asset = get_epub_asset_bytes(row, asset_path)
    if not res_asset:
        handler.send_error(404, "Asset missing in archive")
        return

    data, mime = res_asset
    handler.send_response(200)
    handler.send_header("Content-Type", mime)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "public, max-age=86400")
    handler.end_headers()
    handler.wfile.write(data)


def handle_get_mobi_image(
    handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any], book_id: str
) -> None:
    """GET /api/books/(?P<book_id>\\d+)/mobi_image: Stream embedded record images from MOBI/AZW3."""
    bid = int(book_id)
    q_idx = (qs.get("idx") or ["0"])[0]
    img_idx = int(q_idx or 0)
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT primary_path, archived_path FROM books WHERE id = ?", (bid,)
        ).fetchone()
    finally:
        conn.close()

    if not row or img_idx <= 0:
        handler.send_error(404, "Image not found")
        return

    res_img = get_mobi_image_bytes(row, img_idx)
    if not res_img:
        handler.send_error(404, "Image missing in MOBI/AZW3")
        return

    data, mime = res_img
    handler.send_response(200)
    handler.send_header("Content-Type", mime)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "public, max-age=86400")
    handler.end_headers()
    handler.wfile.write(data)


def handle_get_book_file(
    handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any], book_id: str, mode: str
) -> None:
    """GET /api/books/(?P<book_id>\\d+)/(?P<mode>stream|download): Serve file with Range support or download disposition."""
    bid = int(book_id)
    file_id = (qs.get("file_id") or [""])[0]
    conn = get_conn()
    try:
        if file_id:
            frow = conn.execute(
                "SELECT original_path, archived_path, format FROM book_files WHERE id = ? AND book_id = ?",
                (int(file_id), bid),
            ).fetchone()
        else:
            frow = conn.execute(
                "SELECT primary_path AS original_path, archived_path, primary_format AS format, title FROM books WHERE id = ?",
                (bid,),
            ).fetchone()
    finally:
        conn.close()

    if not frow:
        handler.send_error(404, "Book file not found")
        return

    ext = (frow["format"] or "").lower()
    mime_map = {
        "pdf": "application/pdf",
        "epub": "application/epub+zip",
        "mobi": "application/x-mobipocket-ebook",
        "azw3": "application/vnd.amazon.ebook",
    }
    ctype = mime_map.get(ext, "application/octet-stream")

    final_path = next(
        (
            c
            for c in (frow["archived_path"], frow["original_path"])
            if c and os.path.isfile(c) and os.access(c, os.R_OK)
        ),
        None,
    )
    if final_path is None:
        handler.send_error(404, "Physical file missing or inaccessible")
        return

    as_attachment = (mode == "download")
    range_hdr = handler.headers.get("Range")
    send_range_stream(handler, final_path, range_hdr, ctype, as_attachment=as_attachment)


def _detect_image_mime(head: bytes, ext: str) -> str:
    """Sniff MIME type from header magic bytes with file extension fallback."""
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"RIFF") and len(head) >= 12 and head[8:12] == b"WEBP":
        return "image/webp"
    mime_map = {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif", ".svg": "image/svg+xml"}
    return mime_map.get(ext, "image/jpeg")


COVER_THUMB_WIDTHS = ("360", "720")
COVER_THUMB_MIN_BYTES = 60 * 1024  # smaller originals are cheap to send, and sips would upscale them
COVER_THUMB_DIR = COVERS_DIR / "thumbs"


def _cover_thumbnail(original: Path, width: int) -> Path:
    """Cached JPEG `width` wide (covers are about 2:3) made with macOS sips; the original when it is already
    small or sips fails, so a cover is never lost to a thumbnail error."""
    if original.stat().st_size < COVER_THUMB_MIN_BYTES:
        return original
    thumb = COVER_THUMB_DIR / str(width) / original.name
    if thumb.exists() and thumb.stat().st_mtime >= original.stat().st_mtime:
        return thumb
    thumb.parent.mkdir(parents=True, exist_ok=True)
    tmp = thumb.with_name(f".{thumb.name}.{threading.get_ident()}.tmp")
    cmd = ["sips", "-Z", str(width * 3 // 2), "-s", "format", "jpeg", "-s", "formatOptions", "70",
           str(original), "--out", str(tmp)]
    result = subprocess.run(cmd, capture_output=True, timeout=30)
    if result.returncode != 0 or not tmp.exists():
        logger.warning("Cover thumbnail failed for %s: %s", original.name, result.stderr[:200])
        return original
    os.replace(tmp, thumb)
    return thumb


def handle_get_cover(
    handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any], filename: str
) -> None:
    """GET /covers/(?P<filename>[^/]+)[?w=360|720]: Static serving for book covers with ETag cache; `w` serves a
    thumbnail that wide instead of the original."""
    cfile = COVERS_DIR / filename
    if not (cfile.exists() and os.path.isfile(cfile)):
        handler.send_error(404, "Cover not found")
        return
    width = (qs.get("w") or [""])[0]
    if width in COVER_THUMB_WIDTHS:
        cfile = _cover_thumbnail(cfile, int(width))

    stat = cfile.stat()
    etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
    if_none_match = handler.headers.get("If-None-Match")
    if if_none_match and if_none_match.strip() == etag:
        handler.send_response(304)
        handler.end_headers()
        return

    data = cfile.read_bytes()
    ctype = _detect_image_mime(data[:16], cfile.suffix.lower())
    handler.send_response(200)
    handler.send_header("Content-Type", ctype)
    handler.send_header("ETag", etag)
    handler.send_header("Cache-Control", "public, max-age=604800, immutable")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _serve_bytes_with_etag(
    handler: Any, data: bytes, headers: Dict[str, str]
) -> None:
    """Serve static payload with SHA-256 ETag revalidation (304 on match)."""
    etag = f'"{hashlib.sha256(data).hexdigest()[:16]}"'
    cache_control = headers.get("Cache-Control", "no-cache")
    if (handler.headers.get("If-None-Match") or "").strip() == etag:
        handler.send_response(304)
        handler.send_header("ETag", etag)
        handler.send_header("Cache-Control", cache_control)
        handler.end_headers()
        return

    handler.send_response(200)
    for k, v in headers.items():
        handler.send_header(k, v)
    handler.send_header("ETag", etag)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def handle_get_vendor(
    handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any], filename: str
) -> None:
    """GET /vendor/(?P<filename>[^/]+): Static serving for third-party libraries (e.g. PDF.js) with ETag."""
    vfile = FRONTEND_DIR / "vendor" / filename
    if not (vfile.exists() and os.path.isfile(vfile)):
        handler.send_error(404, "Vendor asset not found")
        return
    mime = (
        "application/javascript"
        if filename.endswith(".js")
        else "text/css"
        if filename.endswith(".css")
        else "application/octet-stream"
    )
    _serve_bytes_with_etag(
        handler,
        vfile.read_bytes(),
        {"Content-Type": mime, "Cache-Control": "public, max-age=86400"},
    )


_ASSET_MIME_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}


def handle_get_asset(
    handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any]
) -> None:
    """GET /assets/<path>: Serve modular frontend CSS/JS with ETag revalidation and traversal guard."""
    raw_path = urllib.parse.urlparse(handler.path).path
    rel_path = urllib.parse.unquote(raw_path[len("/assets/"):]).lstrip("/")
    assets_root = (FRONTEND_DIR / "assets").resolve()
    target = (assets_root / rel_path).resolve()

    if not rel_path or not target.is_relative_to(assets_root) or not target.is_file():
        handler.send_error(404, "Asset not found")
        return

    mime = _ASSET_MIME_TYPES.get(target.suffix.lower(), "application/octet-stream")
    _serve_bytes_with_etag(
        handler,
        target.read_bytes(),
        {"Content-Type": mime, "Cache-Control": "no-cache"},
    )


def handle_get_frontend_spa(
    handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any]
) -> None:
    """GET / and GET /index.html: Serve single page application HTML."""
    idx_file = FRONTEND_DIR / "index.html"
    if idx_file.exists():
        data = idx_file.read_bytes()
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        handler.send_header("Pragma", "no-cache")
        handler.send_header("Expires", "0")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
        return
    handler.send_error(404, "Frontend index.html missing")
