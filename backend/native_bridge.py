import ctypes
import json
import logging
import math
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from config import NATIVE_DYLIB_PATH

logger = logging.getLogger(__name__)

PDF_SIZES_BUFFER_BYTES = 1 << 20  # ~20 bytes a page: room for 50,000 pages
PDF_PAGE_TEXT_BUFFER_BYTES = 1 << 18
PDF_LINES_BUFFER_BYTES = 1 << 21  # dense pages run to ~150 bytes a line


class FallbackNativeBridge:
    """Cross-platform no-op/NumPy fallback when macOS libmacbookai.dylib is unavailable."""

    is_native = False

    def to_pinyin(self, text: str) -> str:
        return ""

    def expand_query(self, query: str) -> Dict:
        return {"tokens": [], "neighbors": {}}

    def parse_pdf(self, pdf_path: str, cover_out_path: str = "", enable_ocr: bool = True) -> Dict:
        return {"ok": False}

    def get_pdf_outline(self, pdf_path: str) -> Dict:
        return {"ok": False}

    @property
    def can_render_pdf_pages(self) -> bool:
        return False

    def pdf_page_sizes(self, pdf_path: str) -> List[List[int]]:
        return []

    def render_pdf_page(self, pdf_path: str, page_index: int, width_px: int, jpeg_quality: float, out_path: str) -> bool:
        return False

    def pdf_page_text(self, pdf_path: str, page_index: int) -> str:
        return ""

    def pdf_page_lines(self, pdf_path: str, page_index: int) -> Dict:
        return {"ok": False}

    def batch_cosine_scores(self, query_vec: List[float], matrix_bytes: bytes, num_rows: int) -> List[float]:
        if num_rows <= 0 or not matrix_bytes:
            return []
        import numpy as np

        mat = np.frombuffer(matrix_bytes, dtype=np.float32).reshape(num_rows, -1)
        q = np.asarray(query_vec[: mat.shape[1]], dtype=np.float32)
        return [float(x) for x in (mat @ q)]


class MacNativeBridge(FallbackNativeBridge):
    is_native = True

    def __init__(self, dylib_path: Path = NATIVE_DYLIB_PATH):
        self.lib = ctypes.CDLL(str(dylib_path))
        self._init_c_signatures()

    def _init_c_signatures(self) -> None:
        """Bind ctypes argument and return types for macOS native C/ObjC entrypoints."""
        lib = self.lib
        lib.mac_embed_text.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_float)]
        lib.mac_embed_text.restype = ctypes.c_int

        lib.mac_expand_query.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
        lib.mac_expand_query.restype = ctypes.c_int

        lib.mac_parse_pdf.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        lib.mac_parse_pdf.restype = ctypes.c_int

        lib.mac_batch_cosine_scores.argtypes = [
            ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float),
            ctypes.c_int, ctypes.POINTER(ctypes.c_float),
        ]
        lib.mac_batch_cosine_scores.restype = ctypes.c_int

        lib.mac_to_pinyin.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
        lib.mac_to_pinyin.restype = ctypes.c_int

        if hasattr(lib, "mac_pdf_outline"):
            lib.mac_pdf_outline.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
            lib.mac_pdf_outline.restype = ctypes.c_int

        if hasattr(lib, "mac_pdf_render_page"):
            lib.mac_pdf_page_sizes.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
            lib.mac_pdf_page_sizes.restype = ctypes.c_int
            lib.mac_pdf_render_page.argtypes = [
                ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_float, ctypes.c_char_p,
            ]
            lib.mac_pdf_render_page.restype = ctypes.c_int
            lib.mac_pdf_page_text.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
            lib.mac_pdf_page_text.restype = ctypes.c_int
            lib.mac_pdf_page_lines.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
            lib.mac_pdf_page_lines.restype = ctypes.c_int

    def to_pinyin(self, text: str) -> str:
        if not text or not text.strip():
            return ""
        buf = ctypes.create_string_buffer(4096)
        n = self.lib.mac_to_pinyin(
            text[:250].encode("utf-8", errors="ignore"), buf, 4096
        )
        if n <= 0:
            return ""
        return buf.value.decode("utf-8", errors="ignore")

    def expand_query(self, query: str) -> Dict:
        if not query or not query.strip():
            return {"tokens": [], "neighbors": {}}
        buf_size = 32768
        buf = ctypes.create_string_buffer(buf_size)
        n = self.lib.mac_expand_query(
            query.encode("utf-8", errors="ignore"), buf, buf_size
        )
        if n <= 0:
            return {"tokens": [], "neighbors": {}}
        try:
            return json.loads(buf.value.decode("utf-8", errors="ignore"))
        except Exception:
            return {"tokens": [], "neighbors": {}}

    def parse_pdf(
        self, pdf_path: str, cover_out_path: str = "", enable_ocr: bool = True
    ) -> Dict:
        buf_size = 65536
        buf = ctypes.create_string_buffer(buf_size)
        n = self.lib.mac_parse_pdf(
            pdf_path.encode("utf-8", errors="ignore"),
            (cover_out_path or "").encode("utf-8", errors="ignore"),
            1 if enable_ocr else 0,
            buf,
            buf_size,
        )
        if n <= 0:
            return {"ok": False}
        try:
            return json.loads(buf.value.decode("utf-8", errors="ignore"))
        except Exception:
            return {"ok": False}

    def get_pdf_outline(self, pdf_path: str) -> Dict:
        if not hasattr(self.lib, "mac_pdf_outline"):
            return {"ok": False}
        buf_size = 65536
        buf = ctypes.create_string_buffer(buf_size)
        n = self.lib.mac_pdf_outline(
            pdf_path.encode("utf-8", errors="ignore"),
            buf,
            buf_size,
        )
        if n <= 0:
            return {"ok": False}
        try:
            return json.loads(buf.value.decode("utf-8", errors="ignore"))
        except Exception:
            return {"ok": False}

    @property
    def can_render_pdf_pages(self) -> bool:
        """False when the dylib predates page rendering (start.sh rebuilds it when the source is newer)."""
        return hasattr(self.lib, "mac_pdf_render_page")

    def pdf_page_sizes(self, pdf_path: str) -> List[List[int]]:
        """Display size in points of every page; [] when the PDF cannot be opened or is encrypted."""
        buf_size = PDF_SIZES_BUFFER_BYTES
        buf = ctypes.create_string_buffer(buf_size)
        n = self.lib.mac_pdf_page_sizes(pdf_path.encode("utf-8"), buf, buf_size)
        if n <= 0:
            return []
        result = json.loads(buf.value.decode("utf-8"))
        return result.get("sizes", []) if result.get("ok") else []

    def render_pdf_page(self, pdf_path: str, page_index: int, width_px: int, jpeg_quality: float, out_path: str) -> bool:
        """Write page `page_index` (0-based) as a JPEG `width_px` wide; False on failure."""
        return self.lib.mac_pdf_render_page(
            pdf_path.encode("utf-8"), page_index, width_px, jpeg_quality, out_path.encode("utf-8")
        ) == 1

    def pdf_page_text(self, pdf_path: str, page_index: int) -> str:
        """Text of page `page_index` (0-based), lines joined by newlines; '' when it has none or cannot be read."""
        buf = ctypes.create_string_buffer(PDF_PAGE_TEXT_BUFFER_BYTES)
        n = self.lib.mac_pdf_page_text(pdf_path.encode("utf-8"), page_index, buf, PDF_PAGE_TEXT_BUFFER_BYTES)
        return buf.raw[:n].decode("utf-8", errors="ignore") if n > 0 else ""

    def pdf_page_lines(self, pdf_path: str, page_index: int) -> Dict:
        """{"ok", "width", "height", "lines": [{"t", "x", "y", "w", "h"}]} of page `page_index` (0-based), boxes in
        points from the top-left; {"ok": False} when the page cannot be read."""
        buf = ctypes.create_string_buffer(PDF_LINES_BUFFER_BYTES)
        n = self.lib.mac_pdf_page_lines(pdf_path.encode("utf-8"), page_index, buf, PDF_LINES_BUFFER_BYTES)
        return json.loads(buf.value.decode("utf-8")) if n > 0 else {"ok": False}

    def batch_cosine_scores(
        self, query_vec: List[float], matrix_bytes: bytes, num_rows: int
    ) -> List[float]:
        if num_rows <= 0 or not matrix_bytes:
            return []
        q_arr = (ctypes.c_float * 300)(*query_vec[:300])
        mat_ptr = ctypes.cast(matrix_bytes, ctypes.POINTER(ctypes.c_float))
        out_arr = (ctypes.c_float * num_rows)()
        self.lib.mac_batch_cosine_scores(q_arr, mat_ptr, num_rows, out_arr)
        return [float(out_arr[i]) for i in range(num_rows)]


_bridge_instance: Optional[FallbackNativeBridge] = None


def create_native_bridge(dylib_path: Path = NATIVE_DYLIB_PATH) -> FallbackNativeBridge:
    """Instantiate MacNativeBridge on macOS when dylib exists, else FallbackNativeBridge."""
    if sys.platform == "darwin" and dylib_path.exists():
        try:
            return MacNativeBridge(dylib_path)
        except OSError as exc:
            logger.warning("Failed to load native bridge %s (%s); using fallback", dylib_path, exc)
    return FallbackNativeBridge()


def get_native_bridge() -> FallbackNativeBridge:
    global _bridge_instance
    if _bridge_instance is None:
        _bridge_instance = create_native_bridge()
    return _bridge_instance
