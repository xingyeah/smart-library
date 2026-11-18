"""Routes of the phone PDF reader: page layout, page images and page text (see pdf_page_service)."""

import logging
from typing import Any, Dict, List

from http_api.responses import send_json
from pdf_page_service import PdfPagesUnavailable, page_blocks, page_image, page_layout

logger = logging.getLogger("http_api.pdf_pages")

# Page URLs carry the source version (?v=), so a cached image can never be stale
IMMUTABLE_CACHE = "private, max-age=31536000, immutable"


def handle_get_pdf_layout(handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any], book_id: str) -> None:
    """GET /api/books/<id>/pdf_layout: page count, page sizes in points and the cache version; 409 when the book
    cannot be rendered here (the reader then falls back to pdf.js)."""
    try:
        layout = page_layout(int(book_id))
    except PdfPagesUnavailable as e:
        logger.info("PDF page mode unavailable: %s", e)
        send_json(handler, {"ok": False, "error": str(e)}, 409)
        return
    source = layout["source"]
    send_json(handler, {"ok": True, "page_count": layout["page_count"], "sizes": layout["sizes"],
                        "version": f"{source['size']:x}-{source['mtime_ns']:x}"})


def handle_get_pdf_page(
    handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any], book_id: str, page_no: str
) -> None:
    """GET /api/books/<id>/pdf_page/<n>: JPEG of page n (1-based)."""
    try:
        image_path = page_image(int(book_id), int(page_no))
    except ValueError as e:
        handler.send_error(404, str(e))
        return
    except PdfPagesUnavailable as e:
        logger.warning("PDF page render failed: %s", e)
        handler.send_error(409, str(e))
        return
    data = image_path.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", "image/jpeg")
    handler.send_header("Cache-Control", IMMUTABLE_CACHE)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def handle_get_pdf_text(
    handler: Any, qs: Dict[str, List[str]], body: Dict[str, Any], book_id: str, page_no: str
) -> None:
    """GET /api/books/<id>/pdf_text/<n>: {"ok", "blocks": [{"text", "role", "top"}], "paragraphs": block texts}
    of page n (1-based)."""
    try:
        blocks = page_blocks(int(book_id), int(page_no))
    except ValueError as e:
        send_json(handler, {"ok": False, "error": str(e)}, 404)
        return
    except PdfPagesUnavailable as e:
        send_json(handler, {"ok": False, "error": str(e)}, 409)
        return
    send_json(handler, {"ok": True, "blocks": blocks, "paragraphs": [b["text"] for b in blocks]})
