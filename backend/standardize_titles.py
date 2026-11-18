"""Standardize all book titles across the library.

1. Fix formatting defects (stray wrapping quotes, unclosed book brackets, newlines in title).
2. Clean garbage titles (e.g. qq ..., SSReader ..., Microsoft Word - ..., KM_C454e...).
3. Extract high-quality Chinese titles from filenames where DB had pure English.
4. Translate remaining pure English titles with DeepSeek to standard published Chinese titles:
   Format: 中文主标题 (Original English Title)
   Subtitle: 中文副标题
5. Update database: title, subtitle, norm_title, pinyin_search.
6. Rebuild SIMD binary vector index.
"""

import json
import logging
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from archiver import rebuild_vector_cache
from config import BASE_DIR, DB_PATH
from database import get_conn
import deepseek_client
from parser import (
    build_cjk_search_tokens,
    clean_spam_text,
    detect_book_language,
    normalize_for_dedup,
    parse_bilingual_filename,
    parse_filename_metadata,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("title_standardizer")

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
LATIN_RE = re.compile(r"[a-zA-Z]")

def clean_title_formatting(title: str) -> str:
    """Strip stray wrapping quotes, extra newlines, and unbalanced book brackets."""
    t = (title or "").strip()
    # Strip wrapping quotes
    while (t.startswith('"') and t.endswith('"')) or (t.startswith("'") and t.endswith("'")):
        t = t[1:-1].strip()
    if t.startswith('"') and not t.endswith('"'):
        t = t[1:].strip()
    if t.endswith('"') and not t.startswith('"'):
        t = t[:-1].strip()

    # Strip stray z-library 'z' prefix before Chinese character
    t = re.sub(r"^[zZ]([\u4e00-\u9fff])", r"\1", t)

    # Replace internal newlines with colon or space
    if "\n" in t or "\r" in t:
        has_colon = "：" in t or ":" in t
        t = re.sub(r"[\r\n]+", " " if has_colon else "：", t).strip()

    # Fix unbalanced leading book marks 《 without closing 》
    if t.startswith("《") and "》" not in t:
        t = t[1:].strip()
    if t.startswith("【") and "】" not in t:
        t = t[1:].strip()

    # Clean double duplicate English parens like (Foo) (Foo)
    t = re.sub(r"[\(（]([^\(\)（）]+)[\)）]\s*[\(（]\1[\)）]$", r"(\1)", t)

    # Clean double spaces
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t

INDESIGN_MAP = {
    270: {"title": "数字化转型与人类行为 (Digital Transformation and Human Behavior)", "author": "U6fonter"},
    363: {"title": "商业信息系统与技术新趋势 (New Trends in Business Information Systems and Technology)", "author": "未知作者"},
    663: {"title": "新技术的风险与监管 (Risks and Regulation of New Technologies)", "author": "Tsuyoshi Matsuda / Jonathan Wolff"},
    1543: {"title": "不确定性下的情感决策：风险、模糊性与黑天鹅 (Affective Decision Making Under Uncertainty)", "author": "Donald J. Brown"},
    1551: {"title": "Transformers权威指南：NLP之外的应用 (Transformers - The Definitive Guide)", "author": "Nicole Koenigstein"},
    7155: {"title": "C++完全指南 (C++ The Comprehensive Guide)", "author": "Torsten T. Will"},
}

def extract_title_from_filenames(paths: List[str], current_title: str) -> Optional[Dict[str, str]]:
    """Try to extract a better bilingual or Chinese title from original filenames."""
    bnames = [os.path.basename(p) for p in paths if p]
    if not bnames:
        return None

    # 1. Check if filename has bilingual structure
    for bn in bnames:
        stem = os.path.splitext(bn)[0]
        bi = parse_bilingual_filename(stem)
        if bi and bi.get("title") and CJK_RE.search(bi["title"]):
            return {
                "title": bi["title"],
                "subtitle": bi.get("subtitle") or "",
                "author": bi.get("author") or "",
            }

    # 2. Check if filename itself is rich in Chinese
    for bn in bnames:
        stem = os.path.splitext(bn)[0]
        # Ignore junk names
        if stem.lower().startswith(("qq", "ssreader", "document", "km_", "dc41f")):
            continue
        # Clean common bracket suffixes e.g. (z-library...), (Unknown), etc.
        stem_clean = re.sub(r"\s*[\(\[（【](?:z-library|z-lib|1lib|sobooks|unknown|duplicate)[^)\]\）】]*[\)\]）】]\s*", "", stem, flags=re.IGNORECASE).strip()
        if CJK_RE.search(stem_clean):
            # Check if has author in parens
            m_auth = re.search(r"[\(（]([^\(\)（）]+)[\)）]\s*$", stem_clean)
            cand_auth = ""
            main_t = stem_clean
            if m_auth:
                cand_auth = m_auth.group(1).strip()
                main_t = stem_clean[:m_auth.start()].strip()
            # If main_t contains '=' e.g. 中文名 = English Title
            if "=" in main_t:
                parts = [p.strip() for p in main_t.split("=", 1)]
                zh_p = parts[0] if CJK_RE.search(parts[0]) else parts[1]
                en_p = parts[1] if zh_p == parts[0] else parts[0]
                return {
                    "title": f"{zh_p} ({en_p})",
                    "subtitle": "",
                    "author": cand_auth,
                }
            elif CJK_RE.search(main_t):
                # If current title is English, keep it as en_part
                if LATIN_RE.search(current_title) and not CJK_RE.search(current_title):
                    return {
                        "title": f"{main_t} ({current_title})",
                        "subtitle": "",
                        "author": cand_auth,
                    }
                else:
                    return {
                        "title": main_t,
                        "subtitle": "",
                        "author": cand_auth,
                    }

    return None

def batch_translate_titles(books_to_translate: List[Dict[str, Any]], batch_size: int = 25) -> Dict[int, Dict[str, str]]:
    """Translate pure English titles to Chinese using DeepSeek in batches."""
    results: Dict[int, Dict[str, str]] = {}
    total = len(books_to_translate)
    logger.info(f"Translating {total} English book titles in batches of {batch_size}...")

    for i in range(0, total, batch_size):
        batch = books_to_translate[i : i + batch_size]
        items_payload = []
        for b in batch:
            items_payload.append({
                "id": b["id"],
                "title": b["title"],
                "subtitle": b.get("subtitle") or "",
                "author": b.get("author") or "",
            })

        prompt = (
            "请为以下英文图书翻译出符合国内中文出版规范、信达雅的中文主书名和中文副标题（如已有国内出版的公认译名，务必采用公认正式译名）。\n"
            "严格输出包含 json 的字典对象，格式如下：\n"
            "{\"translations\": [{\"id\": 1, \"zh_title\": \"中文主标题\", \"zh_subtitle\": \"中文副标题（无则留空）\"}, ...]}\n\n"
            "待翻译图书列表：\n"
            f"{json.dumps(items_payload, ensure_ascii=False)}"
        )

        success = False
        for retry in range(3):
            try:
                res, usage = deepseek_client.chat_json(
                    messages=[
                        {
                            "role": "system",
                            "content": "你是一位资深中外图书编辑与翻译专家，熟知中英双语出版物经典译名与规范。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    feature="translate",
                    timeout=45.0,
                )
                t_list = res.get("translations") or res.get("items") or []
                if isinstance(t_list, list):
                    for item in t_list:
                        bid = item.get("id")
                        if bid is not None:
                            results[int(bid)] = {
                                "zh_title": (item.get("zh_title") or "").strip().replace("《", "").replace("》", ""),
                                "zh_subtitle": (item.get("zh_subtitle") or "").strip(),
                            }
                    success = True
                    break
            except Exception as e:
                logger.warning(f"Batch {i//batch_size + 1} translation retry {retry+1} failed: {e}")
                time.sleep(2.0)

        if not success:
            logger.error(f"Failed to translate batch {i//batch_size + 1}")

        progress = min(total, i + batch_size)
        print(f"  [翻译进度] {progress}/{total} ({progress*100//total}%) 完成", flush=True)

    return results

def run_standardization() -> Dict[str, Any]:
    t0 = time.time()
    conn = get_conn()
    try:
        books = conn.execute("""
            SELECT b.id, b.title, b.subtitle, b.author, b.category, b.summary, b.toc_text,
                   GROUP_CONCAT(bf.original_path, '|||') as paths
            FROM books b
            LEFT JOIN book_files bf ON b.id = bf.book_id
            GROUP BY b.id
        """).fetchall()

        logger.info(f"Auditing total {len(books)} books...")
        
        dirty_fixes = 0
        filename_fixes = 0
        to_translate: List[Dict[str, Any]] = []
        pending_updates: Dict[int, Dict[str, Any]] = {}

        for r in books:
            bid = int(r["id"])
            raw_title = r["title"] or ""
            raw_sub = r["subtitle"] or ""
            raw_auth = r["author"] or ""
            paths = [p for p in (r["paths"] or "").split("|||") if p]

            cleaned_title = clean_title_formatting(raw_title)
            if cleaned_title != raw_title:
                dirty_fixes += 1

            has_cjk = bool(CJK_RE.search(cleaned_title))

            if bid in INDESIGN_MAP:
                mp = INDESIGN_MAP[bid]
                pending_updates[bid] = {
                    "title": mp["title"],
                    "subtitle": raw_sub,
                    "author": mp["author"] if mp.get("author") else raw_auth,
                }
                continue

            # 1. Garbage titles (e.g. qq ..., SSReader ..., Microsoft Word ..., KM_...)
            is_garbage = (
                cleaned_title.lower().startswith(("qq ", "qq2", "ssreader", "microsoft word", "document", "km_", "dc41f"))
                or re.match(r"^(?:doc\d*|index|cover|\d{8,}|\.pdf|.*\.pdf|.*\.epub)$", cleaned_title, re.IGNORECASE)
            )

            if is_garbage or not has_cjk:
                # Try extracting from filenames first
                fn_meta = extract_title_from_filenames(paths, cleaned_title)
                if fn_meta and CJK_RE.search(fn_meta["title"]):
                    pending_updates[bid] = {
                        "title": clean_title_formatting(fn_meta["title"]),
                        "subtitle": fn_meta.get("subtitle") or raw_sub,
                        "author": fn_meta.get("author") or raw_auth,
                    }
                    filename_fixes += 1
                    continue

                # If still pure English and not garbage, queue for DeepSeek translation
                if not is_garbage:
                    to_translate.append({
                        "id": bid,
                        "title": cleaned_title,
                        "raw_title": raw_title,
                        "subtitle": raw_sub,
                        "author": raw_auth,
                    })
            else:
                if cleaned_title != raw_title:
                    pending_updates[bid] = {
                        "title": cleaned_title,
                        "subtitle": raw_sub,
                        "author": raw_auth,
                    }

        logger.info(f"Dirty fixes: {dirty_fixes}, Filename Chinese fixes: {filename_fixes}, Needing AI translation: {len(to_translate)}")

        # 2. Batch AI Translation for pure English books
        ai_translations = batch_translate_titles(to_translate, batch_size=25)
        ai_applied = 0

        for b in to_translate:
            bid = b["id"]
            trans = ai_translations.get(bid)
            orig_en = b["title"]
            if b.get("subtitle"):
                orig_en_full = f"{orig_en} {b['subtitle']}".strip()
            else:
                orig_en_full = orig_en

            if trans and trans.get("zh_title") and CJK_RE.search(trans["zh_title"]):
                zh_t = trans["zh_title"]
                zh_sub = trans.get("zh_subtitle") or b.get("subtitle") or ""
                # Standard bilingual format: 中文书名 (English Title)
                final_title = f"{zh_t} ({orig_en_full})"
                pending_updates[bid] = {
                    "title": final_title,
                    "subtitle": zh_sub,
                    "author": b["author"],
                }
                ai_applied += 1
            else:
                # Fallback: keep original if translation failed
                if b["title"] != b["raw_title"]:
                    pending_updates[bid] = {
                        "title": b["title"],
                        "subtitle": b.get("subtitle") or "",
                        "author": b["author"],
                    }

        # 3. Apply updates to SQLite DB
        logger.info(f"Applying total {len(pending_updates)} standardized title updates to DB...")
        db_updates = []
        for r in books:
            bid = int(r["id"])
            if bid not in pending_updates:
                continue
            up = pending_updates[bid]
            new_title = up["title"]
            new_sub = up["subtitle"]
            new_auth = up["author"] or r["author"] or "未知作者"
            cat = r["category"] or "通识"
            summ = r["summary"] or ""
            toc = r["toc_text"] or ""

            norm_t, norm_a = normalize_for_dedup(new_title, new_auth)
            fts_seed = f"{new_title} {new_sub} {new_auth} {cat} {summ[:400]}"
            pinyin_search = build_cjk_search_tokens(fts_seed, include_pinyin=True)

            db_updates.append((
                new_title,
                new_sub,
                norm_t,
                new_auth,
                norm_a,
                pinyin_search,
                bid,
            ))

        conn.executemany("""
            UPDATE books SET
                title = ?,
                subtitle = ?,
                norm_title = ?,
                author = ?,
                norm_author = ?,
                pinyin_search = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, db_updates)
        conn.commit()

    finally:
        conn.close()

    # 4. Rebuild vector cache
    logger.info("Rebuilding binary SIMD vector index...")
    vec_res = rebuild_vector_cache()
    elapsed = round(time.time() - t0, 2)

    res = {
        "total_books": len(books),
        "dirty_formatting_fixed": dirty_fixes,
        "filename_chinese_recovered": filename_fixes,
        "ai_translated_bilingual": ai_applied,
        "total_updated": len(pending_updates),
        "synced_vectors": vec_res.get("synced_vectors", 0),
        "elapsed_sec": elapsed,
    }
    logger.info(f"Standardization finished in {elapsed}s: {res}")
    return res

if __name__ == "__main__":
    run_standardization()
