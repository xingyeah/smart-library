import html
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _decode_txt_bytes(raw_bytes: bytes) -> str:
    """Attempt decoding text bytes with common Chinese encodings, fallback to utf-8 ignore."""
    for enc in ("utf-8", "utf-8-sig", "gb18030", "big5", "utf-16"):
        try:
            return raw_bytes.decode(enc)
        except Exception:
            continue
    return raw_bytes.decode("utf-8", errors="ignore")


def _parse_txt_chapters(lines: List[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Segment plain text lines into chapters and TOC items."""
    chap_re = re.compile(
        r"^\s*(?:第[0-9一二三四五六七八九十百千万零]+[章回节篇卷部集]|Chapter\s+\d+|Part\s+\d+|序言|前言|导读|楔子|引子|后记|尾声|番外)\b.*",
        re.IGNORECASE,
    )
    chapters: List[Dict[str, Any]] = []
    toc_list: List[Dict[str, Any]] = []
    cur_title = "正文开始"
    cur_paras: List[str] = []

    def _flush_chap(title: str, paras: List[str]) -> None:
        if not paras:
            return
        c_idx = len(chapters)
        html_body = f"<h2>{html.escape(title)}</h2>\n" + "\n".join(f"<p>{html.escape(p)}</p>" for p in paras)
        chapters.append({"index": c_idx, "title": title, "html": html_body})
        toc_list.append({"toc_id": c_idx, "title": title, "chapter_idx": c_idx, "anchor": "", "level": 0})

    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if len(s) <= 55 and chap_re.match(s):
            if cur_paras:
                _flush_chap(cur_title, cur_paras)
                cur_paras = []
            cur_title = s
        else:
            cur_paras.append(s)
            if len(cur_paras) >= 220 and len(chapters) == 0:
                _flush_chap(cur_title, cur_paras)
                cur_paras = []
                cur_title = f"第 {len(chapters) + 1} 节"

    if cur_paras:
        _flush_chap(cur_title, cur_paras)
    if not chapters:
        chapters.append({"index": 0, "title": "全文阅读", "html": "<p>暂无文本内容</p>"})
        toc_list.append({"toc_id": 0, "title": "全文阅读", "chapter_idx": 0, "anchor": "", "level": 0})
    return chapters, toc_list


def build_txt_structure(
    book_id: int,
    fpath: str,
    cache_get: Any,
    cache_put: Any,
) -> Dict[str, Any]:
    """Parse plain TXT file into chapters with clean HTML paragraphs."""
    mtime = os.path.getmtime(fpath)
    cached = cache_get(book_id, mtime)
    if cached is not None:
        return cached

    text = _decode_txt_bytes(Path(fpath).read_bytes())
    chapters, toc_list = _parse_txt_chapters(text.splitlines())
    struct_data = {"mode": "txt", "chapters": chapters, "toc": toc_list}
    cache_put(book_id, mtime, struct_data)
    return struct_data
