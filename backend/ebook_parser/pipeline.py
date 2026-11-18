import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List
from config import COVERS_DIR
from native_bridge import get_native_bridge
from ebook_parser.safe_io import safe_xml, safe_zip_read
from ebook_parser.filenames import (
    clean_spam_text,
    compute_fast_file_hash,
    extract_volume_and_edition,
    normalize_for_dedup,
    parse_filename_metadata,
)
from ebook_parser.language import detect_book_language
from ebook_parser.epub import parse_epub
from ebook_parser.mobi import parse_mobi_azw3


def parse_ebook(file_path: str, source_name: str = "") -> Dict[str, Any]:
    """Parse ebook text & metadata without OCR."""
    return _parse_pipeline(file_path, source_name, enable_ocr=False)


def parse_ebook_with_ocr(file_path: str, source_name: str = "") -> Dict[str, Any]:
    """Parse ebook text & metadata with OCR fallback."""
    return _parse_pipeline(file_path, source_name, enable_ocr=True)


def parse_ebook_full(file_path: str, source_name: str = "", enable_ocr: bool = True) -> Dict[str, Any]:
    """Backward-compatible adapter for parse_ebook."""
    if enable_ocr:
        return parse_ebook_with_ocr(file_path, source_name)
    return parse_ebook(file_path, source_name)


def _parse_pipeline(file_path: str, source_name: str, enable_ocr: bool) -> Dict[str, Any]:
    p = Path(file_path)
    ext = p.suffix.lower()
    file_size = p.stat().st_size if p.exists() else 0
    file_hash = compute_fast_file_hash(file_path)
    cover_file = COVERS_DIR / f"{file_hash}.jpg"
    cover_out = str(cover_file) if not cover_file.exists() else ""

    fn_meta = parse_filename_metadata(file_path)
    embedded: Dict[str, Any] = {}
    used_ocr = False
    page_count = 0

    if ext == ".epub":
        embedded = parse_epub(file_path, cover_out_path=cover_out)
    elif ext in (".azw3", ".mobi", ".azw"):
        embedded = parse_mobi_azw3(file_path, cover_out_path=cover_out)
    elif ext == ".pdf":
        pdf_res = get_native_bridge().parse_pdf(
            file_path, cover_out_path=cover_out, enable_ocr=enable_ocr
        )
        if pdf_res.get("ok"):
            used_ocr = bool(pdf_res.get("used_ocr"))
            page_count = int(pdf_res.get("page_count") or 0)
            kw_tags = [
                k.strip()
                for k in re.split(r"[,，;；\s]+", pdf_res.get("keywords") or "")
                if len(k.strip()) >= 2
            ]
            txt_sample = pdf_res.get("text_sample") or ""
            pdf_pub_year = ""
            m_pdf_yr = re.findall(
                r"(?:copyright\s*(?:©|\(c\))?|©\s*|published\s+in\s+|published\s+by|第一版|第1版|出版时间[:：]?\s*|版次|初版)(?:[^\d]{0,25})([12]\d{3})",
                txt_sample,
                re.IGNORECASE,
            )
            if m_pdf_yr:
                for c in m_pdf_yr:
                    if 1850 <= int(c) <= 2030:
                        pdf_pub_year = c
                        break
            embedded = {
                "title": clean_spam_text(pdf_res.get("title") or ""),
                "author": clean_spam_text(pdf_res.get("author") or ""),
                "pub_year": pdf_pub_year,
                "description": clean_spam_text(pdf_res.get("subject") or ""),
                "tags": kw_tags[:10],
                "toc": pdf_res.get("toc") or [],
                "text_sample": txt_sample,
                "cover_saved": bool(pdf_res.get("cover_saved")),
            }

    emb_title = clean_spam_text(embedded.get("title") or "")
    emb_title = re.sub(r"\s*\(for\s+[^)]*\)\s*", "", emb_title, flags=re.IGNORECASE).strip()

    if fn_meta.get("is_bilingual") == "1":
        final_title = fn_meta["title"]
    elif (
        not emb_title
        or emb_title.lower() in ("unknown", "untitled", "microsoft word", "acrobat", "calibre", "downmagaz.net", "downmagaz.com", "test", "xyz", "pii", "doi")
        or re.match(r"^(?:doc|documento\d*|untitled[\-\d]*|book|index|cover|\d+|b0[a-z0-9]{8}|sme[_-].*|.*\.pdf)$", emb_title.strip(), re.IGNORECASE)
    ):
        final_title = fn_meta["title"]
    else:
        final_title = emb_title

    promo_blurb = ""
    if fn_meta.get("is_bilingual") != "1":
        m_promo = re.search(r"[（\(]([^）\)]{16,})[）\)]\s*$", final_title)
        if m_promo:
            promo_blurb = m_promo.group(1).strip()
            final_title = final_title[: m_promo.start()].strip()

    if " = " in final_title:
        eq_parts = [pt.strip() for pt in final_title.split(" = ", 1)]
        if len(eq_parts[0]) >= 2:
            final_title = eq_parts[0]
            if not embedded.get("subtitle"):
                embedded["subtitle"] = re.sub(r"\([^()]*\)\s*$", "", eq_parts[1]).strip()[:80]

    subtitle = fn_meta.get("subtitle") or embedded.get("subtitle") or ""
    if fn_meta.get("is_bilingual") != "1" and (":" in final_title or "：" in final_title):
        m_sub = re.match(r"^([^:：]{2,35})[:：]\s*(.+)$", final_title)
        if m_sub:
            final_title = m_sub.group(1).strip()
            if not subtitle:
                subtitle = m_sub.group(2).strip()

    emb_author = clean_spam_text(embedded.get("author") or "")
    if (
        not emb_author
        or emb_author.lower() in ("unknown", "admin", "administrator", "user", "calibre", "佚名", "downmagaz.net", "downmagaz.com", "test", "carlopez", "mark")
        or "公众号" in emb_author
        or "www." in emb_author.lower()
    ):
        final_author = fn_meta["author"] or "未知作者"
    else:
        final_author = emb_author

    raw_desc = embedded.get("description") or ""
    if promo_blurb and promo_blurb not in raw_desc:
        raw_desc = f"{promo_blurb}。{raw_desc}".strip("。")

    vol_part, ed_tag = extract_volume_and_edition(f"{final_title} {subtitle} {p.stem}")
    if not vol_part:
        vol_part = fn_meta.get("volume_part", "")
    if not ed_tag:
        ed_tag = fn_meta.get("edition_tag", "")

    norm_title, norm_author = normalize_for_dedup(final_title, final_author)
    dedup_key = f"{norm_title}||{norm_author}||{vol_part}||{ed_tag}"

    has_cover = cover_file.exists() and cover_file.stat().st_size > 400
    cover_rel = f"/covers/{file_hash}.jpg" if has_cover else ""
    toc_list = embedded.get("toc") or []
    detected_lang = detect_book_language(
        bnames=[os.path.basename(file_path)],
        title=final_title,
        subtitle=subtitle,
        author=final_author,
        translator=embedded.get("translator") or "",
        toc_text=" | ".join(str(x) for x in toc_list[:20]),
        primary_path=file_path,
        is_bilingual=fn_meta.get("is_bilingual") == "1",
    )

    return {
        "file_path": file_path,
        "source_name": source_name,
        "format": ext.lstrip("."),
        "file_size": file_size,
        "file_hash": file_hash,
        "dedup_key": dedup_key,
        "title": final_title,
        "subtitle": subtitle,
        "norm_title": norm_title,
        "author": final_author,
        "norm_author": norm_author,
        "translator": embedded.get("translator") or "",
        "publisher": embedded.get("publisher") or "",
        "pub_year": embedded.get("pub_year") or "",
        "isbn": embedded.get("isbn") or "",
        # Raw in-file metadata before the filename fallback, kept per file for same-work checks
        "embedded_title": emb_title,
        "embedded_author": emb_author,
        "douban_id": embedded.get("douban_id") or "",
        "series": embedded.get("series") or "",
        "volume_part": vol_part,
        "edition_tag": ed_tag,
        "language": detected_lang,
        "tags": embedded.get("tags") or [],
        "raw_description": raw_desc,
        "toc": toc_list,
        "text_sample": embedded.get("text_sample") or "",
        "page_count": page_count,
        "used_ocr": used_ocr,
        "cover_path": cover_rel,
    }
