import html
import logging
import os
import struct
import urllib.parse
import zipfile
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple
from native_bridge import get_native_bridge
from parser import safe_zip_read, strip_html
from book_reader.sanitizer import (
    resolve_zip_path,
    sanitize_and_format_book_html,
)
from book_reader.epub import build_epub_structure
from book_reader.mobi import build_mobi_azw3_structure
from book_reader.text import build_txt_structure

logger = logging.getLogger(__name__)

_BOOK_STRUCT_CACHE: "OrderedDict[Tuple[int, float], Dict[str, Any]]" = OrderedDict()
_MAX_CACHE_BOOKS = 12


def _chapter_list(chapters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for c in chapters:
        if "chars" not in c:
            c["chars"] = len(strip_html(c.get("html") or ""))
    return [{"index": c["index"], "title": c["title"], "chars": c["chars"]} for c in chapters]


def cache_get(book_id: int, mtime: float) -> Optional[Dict[str, Any]]:
    key = (book_id, mtime)
    if key in _BOOK_STRUCT_CACHE:
        _BOOK_STRUCT_CACHE.move_to_end(key)
        return _BOOK_STRUCT_CACHE[key]
    return None


def cache_put(book_id: int, mtime: float, data: Dict[str, Any]) -> None:
    key = (book_id, mtime)
    _BOOK_STRUCT_CACHE[key] = data
    _BOOK_STRUCT_CACHE.move_to_end(key)
    while len(_BOOK_STRUCT_CACHE) > _MAX_CACHE_BOOKS:
        _BOOK_STRUCT_CACHE.popitem(last=False)


def extract_book_reader_content(book_row: Any, chapter_idx: int = 0) -> Dict[str, Any]:
    """Unified high-precision reader extractor for EPUB, MOBI, AZW3/KF8, PDF, and TXT."""
    book_id = int(book_row["id"])
    title = book_row["title"] or "未命名书籍"
    author = book_row["author"] or "未知作者"
    fmt = (book_row["primary_format"] or "").lower()

    fpath = None
    for candidate in [book_row["archived_path"], book_row["primary_path"]]:
        if candidate and os.path.exists(candidate):
            try:
                with open(candidate, "rb") as test_f:
                    test_f.read(16)
                fpath = candidate
                break
            except Exception as err:
                logger.debug("Candidate path not readable %s: %s", candidate, err)
    if not fpath:
        return _build_fallback_guide(book_row, "物理文件不存在或外部磁盘未挂载")

    if fmt == "pdf":
        toc_items: List[Dict[str, Any]] = []
        page_count = int(book_row["page_count"] or 0)
        try:
            bridge = get_native_bridge()
            if hasattr(bridge, "get_pdf_outline"):
                pdf_info = bridge.get_pdf_outline(fpath)
                if pdf_info and pdf_info.get("ok"):
                    page_count = int(pdf_info.get("page_count") or page_count)
                    for idx, item in enumerate(pdf_info.get("toc") or []):
                        toc_items.append(
                            {
                                "toc_id": idx,
                                "title": item.get("title") or f"第 {idx + 1} 节",
                                "chapter_idx": int(item.get("page") or 1),
                                "page": int(item.get("page") or 1),
                                "anchor": "",
                                "level": int(item.get("level") or 0),
                            }
                        )
        except Exception as err:
            logger.debug("Failed extracting PDF outline for %s: %s", fpath, err)

        if not toc_items and book_row["toc_text"]:
            raw_tocs = [x.strip() for x in (book_row["toc_text"] or "").split("|") if x.strip()]
            for idx, lbl in enumerate(raw_tocs):
                toc_items.append(
                    {
                        "toc_id": idx,
                        "title": lbl,
                        "chapter_idx": idx + 1,
                        "page": 1,
                        "anchor": "",
                        "level": 0,
                    }
                )

        return {
            "ok": True,
            "mode": "pdf",
            "book_id": book_id,
            "book_title": title,
            "author": author,
            "language": (book_row["language"] or "").lower(),
            "format": "pdf",
            "pdf_url": f"/api/books/{book_id}/stream",
            "page_count": page_count,
            "toc": toc_items,
            "chapters": [{"index": t["toc_id"], "title": t["title"], "page": t["page"]} for t in toc_items],
            "current_chapter": 0,
            "total_chapters": len(toc_items),
        }

    if fmt == "epub":
        try:
            st = build_epub_structure(book_id, fpath, cache_get, cache_put)
            chapters = st["chapters"]
            toc_list = st["toc"]
            href_to_spine = st["href_to_spine"]

            if not chapters:
                return _build_fallback_guide(book_row, "EPUB 内未检测到有效正文章节")

            c_idx = max(0, min(chapter_idx, len(chapters) - 1))
            ch_meta = chapters[c_idx]
            with zipfile.ZipFile(fpath, "r") as zf:
                raw_html = safe_zip_read(zf, ch_meta["file"]).decode("utf-8", errors="ignore")

            formatted_html = sanitize_and_format_book_html(
                raw_html,
                book_id=book_id,
                doc_dir=ch_meta["dir"],
                href_to_spine=href_to_spine,
                is_mobi=False,
            )
            return {
                "ok": True,
                "mode": "html",
                "book_id": book_id,
                "book_title": title,
                "author": author,
                "language": (book_row["language"] or "").lower(),
                "format": fmt,
                "toc": toc_list,
                "chapters": _chapter_list(chapters),
                "current_chapter": c_idx,
                "total_chapters": len(chapters),
                "chapter_title": ch_meta["title"],
                "html": formatted_html,
                "content": strip_html(formatted_html)[:25000],
            }
        except Exception as e:
            logger.warning("EPUB parse error for book %s: %s", book_id, e)
            return _build_fallback_guide(book_row, f"EPUB 解析受限 ({e})")

    if fmt in ("mobi", "azw3", "kf8"):
        try:
            st = build_mobi_azw3_structure(book_id, fpath, cache_get, cache_put)
            chapters = st["chapters"]
            toc_list = st["toc"]
            if not chapters:
                return _build_fallback_guide(book_row, "MOBI/AZW3 未能提取出有效正文章节")

            c_idx = max(0, min(chapter_idx, len(chapters) - 1))
            ch_meta = chapters[c_idx]
            return {
                "ok": True,
                "mode": "html",
                "book_id": book_id,
                "book_title": title,
                "author": author,
                "language": (book_row["language"] or "").lower(),
                "format": fmt,
                "toc": toc_list,
                "chapters": _chapter_list(chapters),
                "current_chapter": c_idx,
                "total_chapters": len(chapters),
                "chapter_title": ch_meta["title"],
                "html": ch_meta["html"],
                "content": strip_html(ch_meta["html"])[:25000],
            }
        except Exception as e:
            logger.warning("MOBI/AZW3 parse error for book %s: %s", book_id, e)
            return _build_fallback_guide(book_row, f"MOBI/AZW3 解析受限 ({e})")

    if fmt == "txt":
        try:
            st = build_txt_structure(book_id, fpath, cache_get, cache_put)
            chapters = st["chapters"]
            toc_list = st["toc"]
            c_idx = max(0, min(chapter_idx, len(chapters) - 1))
            ch_meta = chapters[c_idx]
            return {
                "ok": True,
                "mode": "html",
                "book_id": book_id,
                "book_title": title,
                "author": author,
                "language": (book_row["language"] or "").lower(),
                "format": fmt,
                "toc": toc_list,
                "chapters": _chapter_list(chapters),
                "current_chapter": c_idx,
                "total_chapters": len(chapters),
                "chapter_title": ch_meta["title"],
                "html": ch_meta["html"],
                "content": strip_html(ch_meta["html"])[:25000],
            }
        except Exception as e:
            logger.warning("TXT parse error for book %s: %s", book_id, e)
            return _build_fallback_guide(book_row, f"TXT 解析受限 ({e})")

    return _build_fallback_guide(book_row, "")


def _build_fallback_guide(book_row: Any, warning_msg: str = "") -> Dict[str, Any]:
    book_id = int(book_row["id"])
    title = book_row["title"] or "未命名书籍"
    author = book_row["author"] or "未知作者"
    fmt = (book_row["primary_format"] or "").lower()
    summary = html.escape(book_row["summary"] or "暂无简介")
    core = html.escape(book_row["core_points"] or "暂无核心要点")
    aud = html.escape(book_row["target_audience"] or "大众读者")
    toc_lines = [html.escape(x.strip()) for x in (book_row["toc_text"] or "").split("|") if x.strip()]
    toc_html = "<ul>" + "".join(f"<li>{x}</li>" for x in toc_lines) + "</ul>" if toc_lines else "<p>暂无目录信息</p>"

    fallback_html = (
        f"<h2>📖 《{html.escape(title)}》结构化导览</h2>"
        f"<h3>一、核心内容简介</h3><p>{summary}</p>"
        f"<h3>二、核心知识要点</h3><p>{core}</p>"
        f"<h3>三、适合读者群</h3><p>{aud}</p>"
        f"<h3>四、全书章节脉络</h3>{toc_html}"
    )
    res: Dict[str, Any] = {
        "ok": True,
        "mode": "html",
        "book_id": book_id,
        "book_title": title,
        "author": author,
        "format": fmt,
        "toc": [{"toc_id": 0, "title": "全书内容与章节导览", "chapter_idx": 0, "anchor": "", "level": 0}],
        "chapters": [{"index": 0, "title": "全书内容与章节导览"}],
        "current_chapter": 0,
        "total_chapters": 1,
        "chapter_title": "全书内容与章节导览",
        "html": fallback_html,
        "content": strip_html(fallback_html),
    }
    if warning_msg:
        res["warning"] = warning_msg
    return res


def get_epub_asset_bytes(book_row: Any, asset_path: str) -> Optional[Tuple[bytes, str]]:
    fpath = book_row["primary_path"]
    if not fpath or not os.path.exists(fpath):
        fpath = book_row["archived_path"]
    if not fpath or not os.path.exists(fpath):
        return None

    clean_target = urllib.parse.unquote(asset_path).lstrip("/")
    try:
        with zipfile.ZipFile(fpath, "r") as zf:
            namelist = zf.namelist()
            actual = None
            if clean_target in namelist:
                actual = clean_target
            else:
                low_target = clean_target.lower()
                base_target = os.path.basename(low_target)
                for n in namelist:
                    if n.lower() == low_target or n.lower().endswith("/" + base_target):
                        actual = n
                        break
            if not actual:
                return None
            data = safe_zip_read(zf, actual)
            ext = os.path.splitext(actual)[1].lower()
            mime_map = {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".gif": "image/gif",
                ".webp": "image/webp",
                ".svg": "image/svg+xml",
            }
            return data, mime_map.get(ext, "application/octet-stream")
    except Exception as err:
        logger.debug("Failed reading epub asset %s: %s", asset_path, err)
        return None


def get_mobi_image_bytes(book_row: Any, img_offset_1based: int) -> Optional[Tuple[bytes, str]]:
    fpath = book_row["primary_path"]
    if not fpath or not os.path.exists(fpath):
        fpath = book_row["archived_path"]
    if not fpath or not os.path.exists(fpath) or img_offset_1based <= 0:
        return None

    try:
        with open(fpath, "rb") as f:
            header = f.read(78)
            if len(header) < 78:
                return None
            num_records = struct.unpack(">H", header[76:78])[0]
            rec_info = f.read(num_records * 8)
            offsets = [struct.unpack(">I", rec_info[i * 8 : i * 8 + 4])[0] for i in range(num_records)]
            f.seek(0, os.SEEK_END)
            offsets.append(f.tell())

            f.seek(offsets[0])
            rec0 = f.read(offsets[1] - offsets[0])
            if len(rec0) < 112:
                return None
            first_image_idx = struct.unpack(">I", rec0[108:112])[0]
            target_rec = first_image_idx + img_offset_1based - 1
            if target_rec <= 0 or target_rec >= num_records:
                return None

            s, e = offsets[target_rec], offsets[target_rec + 1]
            if e <= s or e - s > 15 * 1024 * 1024:
                return None
            f.seek(s)
            data = f.read(e - s)
            if data.startswith(b"\xff\xd8\xff"):
                return data, "image/jpeg"
            if data.startswith(b"\x89PNG"):
                return data, "image/png"
            if data.startswith(b"GIF8"):
                return data, "image/gif"
            return data, "image/jpeg"
    except Exception as err:
        logger.debug("Failed reading mobi image %s: %s", img_offset_1based, err)
        return None
