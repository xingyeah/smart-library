import json
import os
import re
import sqlite3
import struct
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from archiver import rebuild_vector_cache
from config import BASE_DIR, DB_PATH, INDEX_CSV_PATHS
from database import get_conn
from parser import (
    build_cjk_search_tokens,
    clean_spam_text,
    detect_book_language,
    normalize_for_dedup,
    parse_bilingual_filename,
    parse_filename_metadata,
)

XLSX_PATH = BASE_DIR / "电子书分类索引与简介目录.xlsx"

# Strict anti-boilerplate marketing fluff blacklist
BOILERPLATE_PATTERNS = [
    r"(?:本书)?(?:文笔优美|发人深省|开山之作|不可多得|强烈推荐|不容错过|阅读盛宴|必读书目|大师力作|震撼人心|引人入胜|深入浅出地介绍了|通俗易懂地介绍了|具有极高的学术价值|堪称经典|值得一读|带你走进|领略.*的魅力|不容置疑|不可磨灭|里程碑式的?著作|殿堂级|必读之作|值得细细品读)",
    r"(?:是一部|堪称一部|这是一本|这是一部|本书是)?(?:非常|极其|特别|不可多得的)?(?:优秀|精彩|生动|详实|权威|经典)的?(?:著作|读物|作品|指南|教程|好书)",
    r"(?:无论你是.*还是.*|只要你.*就能.*|让你在.*中.*)",
]

BOILERPLATE_REGEX = re.compile("|".join(BOILERPLATE_PATTERNS), re.IGNORECASE)

NOISE_TOC_REGEX = re.compile(
    r"^(?:封面|扉页|版权|版权信息|版权页|图书在版编目|CIP|目录|contents|table of contents|cover|cover page|title\s*page|half\s*title|copyright|dedication|endorsements|acknowledgments|acknowledgements|about the author|about the publisher|also by.*|by the same author.*|notes|index|bibliography|references|glossary|appendix|preface|foreword|致谢|献给|由衷感谢|序言|前言|导读|推荐序|中文版序|译者序|自序|后记|结语|附录|参考文献|索引)$",
    re.IGNORECASE,
)

SPECIAL_CODE_BOOKS = {
    "mcs.pdf": {
        "title": "计算机科学数学基础讲义 (Mathematics for Computer Science)",
        "subtitle": "MIT 离散数学、数理逻辑、图论与渐进分析经典教材",
        "author": "Eric Lehman / F. Thomson Leighton / Albert R. Meyer",
    },
    "MT4R-chn-v0.1.4.pdf": {
        "title": "MT4R 量化金融与 R 语言交互手册 (MT4R Reference)",
        "subtitle": "MetaTrader 4 与 R 语言策略回测接口指南",
        "author": "MT4R Team",
    },
}


def clean_anti_boilerplate(text: str) -> str:
    """Strip marketing fluff, promotional rhetoric, and appended TOC suffixes, keeping pure knowledge & methodologies."""
    if not text:
        return ""
    cleaned = text
    # Strip appended TOC outline suffix
    cleaned = re.sub(r"\s*全书核心脉络涵盖：.*$", "", cleaned, flags=re.DOTALL)
    for pat in BOILERPLATE_PATTERNS:
        cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^[，,。；;、\s]+|[，,。；;、\s]+$", "", cleaned)
    cleaned = re.sub(r"[，,]{2,}", "，", cleaned)
    cleaned = re.sub(r"[。]{2,}", "。", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def normalize_category_name(raw_cat: str) -> str:
    """Convert '01_数学与统计' to '数学与统计'."""
    if not raw_cat:
        return "综合通识"
    return re.sub(r"^\d+_", "", raw_cat.strip())


def load_xlsx_records(xlsx_path: Path = XLSX_PATH) -> Dict[str, Dict[str, str]]:
    """Parse curated catalog from CSV or XLSX and map filename -> {category, clean_category, summary, source_name, pub_year}."""
    t0 = time.time()
    records: Dict[str, Dict[str, str]] = {}

    csv_paths = [*INDEX_CSV_PATHS, BASE_DIR / "电子书分类索引与简介目录.csv"]
    for cp in csv_paths:
        if cp.exists():
            import csv
            try:
                with open(cp, "r", encoding="utf-8-sig") as fp:
                    reader = csv.DictReader(fp)
                    for row in reader:
                        fname = (row.get("电子书文件名") or row.get("电子书文件名（规范化）") or row.get("文件名") or "").strip()
                        if not fname:
                            continue
                        cat = (row.get("分类名称") or "").strip()
                        summary = (row.get("核心内容简要介绍") or row.get("简要内容介绍（精准学术）") or row.get("简介") or "").strip()
                        src = (row.get("书库来源") or "kindle电子书").strip()
                        year = (row.get("出版年份") or "").strip()
                        rec = {
                            "raw_category": cat,
                            "category": normalize_category_name(cat),
                            "summary": clean_anti_boilerplate(summary),
                            "summary_source": (row.get("简介来源") or "").strip(),
                            "source_name": src,
                            "pub_year": year,
                        }
                        records[fname] = rec
                        records[fname.lower()] = rec
                if records:
                    return records
            except Exception as e:
                print(f"[catalog] CSV load error {cp}: {e}", flush=True)

    if not xlsx_path.exists():
        return records

    with zipfile.ZipFile(str(xlsx_path), "r") as zf:
        sheet_xml = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
        for row_el in sheet_xml.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"):
            c_list = row_el.findall("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c")
            if len(c_list) >= 6:
                def get_val(c):
                    t = c.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}is/{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")
                    v = c.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v")
                    return (t.text if t is not None else (v.text if v is not None else "")) or ""

                raw_cat = get_val(c_list[1])
                fname = get_val(c_list[2])
                raw_summary = get_val(c_list[5])
                src_name = get_val(c_list[4]) if len(c_list) > 4 else ""

                if not fname or fname == "电子书名称":
                    continue

                clean_sum = clean_anti_boilerplate(raw_summary)
                clean_cat = normalize_category_name(raw_cat)
                rec = {
                    "raw_category": raw_cat,
                    "category": clean_cat,
                    "summary": clean_sum,
                    "source_name": src_name,
                    "pub_year": "",
                }
                records[fname] = rec
                records[fname.lower()] = rec
    return records


def run_summary_optimization() -> Dict[str, Any]:
    t0 = time.time()
    xlsx_records = load_xlsx_records()
    print(f"Loaded {len(xlsx_records)//2} curated records from {XLSX_PATH.name}")

    conn = get_conn()
    try:
        # Load all file paths per book_id
        file_rows = conn.execute("SELECT book_id, original_path, is_primary FROM book_files ORDER BY is_primary DESC, id ASC").fetchall()
        files_by_book: Dict[int, List[str]] = {}
        for fr in file_rows:
            files_by_book.setdefault(int(fr["book_id"]), []).append(fr["original_path"] or "")

        rows = conn.execute(
            "SELECT id, title, subtitle, author, category, summary, summary_source, core_points, toc_text FROM books"
        ).fetchall()

        matched_count = 0
        bilingual_repaired = 0
        updated_count = 0

        updates = []
        for r in rows:
            bid = int(r["id"])
            paths = files_by_book.get(bid) or []
            bnames = [os.path.basename(p) for p in paths if p]

            rec = None
            for bn in bnames:
                rec = xlsx_records.get(bn) or xlsx_records.get(bn.lower())
                if rec:
                    break

            title_clean = (r["title"] or "").strip()
            subtitle_clean = (r["subtitle"] or "").strip()
            auth_clean = (r["author"] or "未知作者").strip()

            # Strip watermark noise like (for True Epub) or (for . .)
            title_clean = re.sub(r"\s*\(for\s+[^)]*\)\s*", "", title_clean, flags=re.IGNORECASE).strip()

            # Check special code books first (e.g. mcs.pdf)
            for bn in bnames:
                if bn in SPECIAL_CODE_BOOKS:
                    sp = SPECIAL_CODE_BOOKS[bn]
                    title_clean = sp["title"]
                    subtitle_clean = sp["subtitle"]
                    auth_clean = sp["author"]
                    bilingual_repaired += 1
                    break

            # Try bilingual filename extraction across all files of this book
            bi_info = None
            for bn in bnames:
                stem = os.path.splitext(bn)[0]
                cand_bi = parse_bilingual_filename(stem)
                if cand_bi:
                    if not bi_info or (cand_bi.get("author") and not bi_info.get("author")):
                        bi_info = cand_bi

            if bi_info:
                title_clean = bi_info["title"]
                if bi_info["subtitle"]:
                    subtitle_clean = bi_info["subtitle"]
                # If author was mistakenly set to the Chinese translated title or is garbage/unknown, restore real author
                zh_t = bi_info["zh_title"]
                if (
                    auth_clean == zh_t
                    or (zh_t and zh_t in auth_clean)
                    or auth_clean.lower() in ("未知作者", "unknown", "downmagaz.net", "downmagaz.com", "test", "carlopez", "mark")
                ):
                    if bi_info["author"]:
                        auth_clean = bi_info["author"]
                    else:
                        # Strip (zh_title) from auth_clean if present
                        cleaned_a = re.sub(r"[\(（]" + re.escape(zh_t) + r"[\)）]", "", auth_clean).strip()
                        auth_clean = cleaned_a if cleaned_a and cleaned_a != zh_t else "未知作者"
                bilingual_repaired += 1
            else:
                # Check if title is garbage embedded metadata (e.g. 0321994604.pdf, downmagaz.net, test, Documento1, PII, B0DF3TMXBY)
                if (
                    title_clean.lower() in ("unknown", "untitled", "microsoft word", "acrobat", "calibre", "downmagaz.net", "downmagaz.com", "test", "xyz", "pii", "doi")
                    or re.match(r"^(?:doc|documento\d*|untitled[\-\d]*|index|cover|\d+|b0[a-z0-9]{8}|sme[_-].*|.*\.pdf)$", title_clean, re.IGNORECASE)
                ) and bnames:
                    fn_m = parse_filename_metadata(paths[0])
                    if fn_m.get("title"):
                        title_clean = fn_m["title"]
                    if fn_m.get("subtitle") and not subtitle_clean:
                        subtitle_clean = fn_m["subtitle"]
                    if fn_m.get("author") and auth_clean.lower() in ("未知作者", "unknown", "downmagaz.net", "downmagaz.com", "test", "carlopez", "mark"):
                        auth_clean = fn_m["author"]

            existing_source = (r["summary_source"] or "").strip()
            is_protected = existing_source in ("ai_refreshed", "manual")
            if is_protected:
                pure_summary = (r["summary"] or "").strip()
                clean_category = normalize_category_name(r["category"] or "综合通识")
                sum_source = existing_source
            elif rec:
                matched_count += 1
                pure_summary = clean_anti_boilerplate(rec["summary"])
                clean_category = rec["category"]
                rec_src = (rec.get("summary_source") or "").strip()
                sum_source = rec_src if rec_src in ("ai_refreshed", "manual") else "xlsx_curated"
            else:
                pure_summary = clean_anti_boilerplate(r["summary"] or "")
                clean_category = normalize_category_name(r["category"] or "综合通识")
                sum_source = "l2_cleaned"

            # Filter book's TOC to extract clean, genuine chapters (strip front-matter/back-matter noise)
            toc_raw = r["toc_text"] or ""
            raw_chaps = [ch.strip() for ch in toc_raw.split(" | ") if ch.strip()]
            clean_chaps = [
                ch for ch in raw_chaps
                if len(ch) >= 2
                and not NOISE_TOC_REGEX.match(ch)
                and not re.search(
                    r"(?:版权|all rights reserved|由衷感谢|献给|作者授权|title\s*page|cover\s*page|dedication|also\s+by|about\s+the\s+author|acknowledg|copyright|endorsements|half\s*title)",
                    ch,
                    re.IGNORECASE,
                )
            ]
            clean_toc_text = " | ".join(clean_chaps)

            # Generate 3 precise Reverse-HyDE natural language queries based on pure summary + bilingual title
            auth_for_q = auth_clean if auth_clean != "未知作者" else ""
            q1 = f"推荐关于{clean_category}领域《{title_clean}》的核心著作"
            q2 = f"讲{'、'.join(clean_chaps[:3])}的{clean_category}书籍" if clean_chaps else f"深入剖析{clean_category}核心逻辑与前沿实践的代表作"
            q3 = f"{auth_for_q}论述{clean_category}核心命题与知识体系的专业书".strip()
            hyde_queries = [q1, q2, q3]

            norm_title, norm_author = normalize_for_dedup(title_clean, auth_clean)

            # Re-tokenize CJK + Pinyin + Latin for SQLite FTS5
            fts_seed = f"{title_clean} {subtitle_clean} {auth_for_q} {clean_category} {pure_summary[:450]} {' '.join(clean_chaps[:12])}"
            pinyin_search = build_cjk_search_tokens(fts_seed, include_pinyin=True)

            pub_year = (rec.get("pub_year") or "").strip() if rec else ""

            kept_core_points = (r["core_points"] or "[]") if is_protected else json.dumps(clean_chaps[:6], ensure_ascii=False)
            updates.append((
                title_clean,
                subtitle_clean,
                norm_title,
                auth_clean,
                norm_author,
                clean_category,
                pure_summary,
                sum_source,
                pub_year,
                pub_year,
                clean_toc_text,
                json.dumps(hyde_queries, ensure_ascii=False),
                kept_core_points,
                pinyin_search,
                bid,
            ))

        print(f"Matched {matched_count} / {len(rows)} books ({bilingual_repaired} bilingual/title-repaired). Updating database & re-indexing...")
        conn.executemany(
            """
            UPDATE books SET
                title = ?,
                subtitle = ?,
                norm_title = ?,
                author = ?,
                norm_author = ?,
                category = ?,
                summary = ?,
                summary_source = ?,
                pub_year = CASE WHEN ? != '' THEN ? ELSE pub_year END,
                toc_text = ?,
                hyde_queries = ?,
                core_points = ?,
                pinyin_search = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            updates,
        )
        conn.commit()
        updated_count = len(updates)
    finally:
        conn.close()

    print("Rebuilding binary SIMD vector index...")
    vec_res = rebuild_vector_cache()
    elapsed = round(time.time() - t0, 2)
    res = {
        "total_books": len(rows),
        "matched_with_xlsx": matched_count,
        "bilingual_repaired": bilingual_repaired,
        "updated_books": updated_count,
        "synced_vectors": vec_res.get("synced_vectors", 0),
        "elapsed_sec": elapsed,
    }
    print(f"Optimization finished in {elapsed}s: {res}")
    return res


if __name__ == "__main__":
    run_summary_optimization()
