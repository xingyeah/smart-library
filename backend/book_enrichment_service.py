"""
Book Metadata & Summary AI Enrichment Service.

Re-analyzes authentic book table-of-contents and sample body content with DeepSeek
to correct misplaced categories, hallucinated synopses, and bloated titles,
and relocates physical library hardlinks accordingly.
"""

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from archiver import ArchiveTarget, safe_hardlink_to_library
from book_reader.payload import extract_book_reader_content
from config import LIBRARY_DIR, is_path_within
from database import get_conn, write_tx
import deepseek_client as ds
from ebook_parser import build_cjk_pinyin_search_tokens, strip_html
import enrich_ai
from http_api.background import spawn_reindex

logger = logging.getLogger("book_enrichment_service")

SEARCH_TOKEN_SUMMARY_CHARS = 300
FALLBACK_GUIDE_TITLE = "全书内容与章节导览"  # chapter title of book_reader's fallback page


class BookNotFoundError(LookupError):
    pass


class NoReadableTextError(ValueError):
    """The book gave no body text, so the model would only be guessing from the title."""

LEVELS = ("入门", "普及", "进阶", "专业")
KINDS = ("技术工程", "学术理论", "实用指南", "投资理财", "纪实调查", "传记回忆", "历史叙事", "小说文学", "随笔评论", "套装合集")
CATEGORIES = (
    "历史与地缘政治", "文学小说与艺术", "商业管理与创投", "个人成长与生活", "社科政治与法律", "自然科学与健康", "金融与经济学",
    "哲学宗教与通识", "心理学与脑科学", "数学与统计", "人工智能与技术", "人物传记与回忆录", "销售与市场营销", "产品与UX设计",
)

SYSTEM_PROMPT = (
    "你是一位资深多学科图书情报专家。当前电子书元数据（书名、分类或简介）此前可能存在归类错误或内容串门。\n"
    "请根据该书真实的目录章节（TOC）与正文切片进行严谨甄别与全面重构，输出严格 JSON，格式为：\n"
    "{\n"
    '  "clean_title": "清洗后的准确书名（去除【促销语】、格式后缀。重要标准：若原书为外文译作或原书名含英文，必须严格保留「中文名 (English Title)」的双语标准命名，严禁删除英文书名！纯中文或纯英文书则保留原主标题）",\n'
    '  "category": "所属领域，必须严格在以下14个之一：' + '、'.join(CATEGORIES) + '",\n'
    '  "summary": "150-300字中文内容简介，客观概括核心论题、论据脉络与学术或实用价值，严禁华而不实的营销套话",\n'
    '  "core_points": ["核心要点1", "核心要点2", "核心要点3"],\n'
    '  "target_audience": "适合读者群体的清晰定义",\n'
    '  "keywords": ["关键词1", "关键词2", "关键词3", "关键词4", "关键词5"],\n'
    '  "level": "难度，只能是 入门/普及/进阶/专业 之一",\n'
    '  "kind": "类型，只能是 技术工程/学术理论/实用指南/投资理财/纪实调查/传记回忆/历史叙事/小说文学/随笔评论/套装合集 之一",\n'
    '  "subfield": "2-6字细分领域方向"\n'
    "}"
)


def _extract_sample_content(book_row: Any) -> str:
    """Extract representative raw body text from the book's first few readable chapters."""
    snippets: List[str] = []
    total_len = 0

    for ch_idx in range(1, 10):
        try:
            res = extract_book_reader_content(book_row, chapter_idx=ch_idx)
            if res.get("chapter_title") == FALLBACK_GUIDE_TITLE:
                break  # the reader could not open the file and returned our own stored summary, not book text
            raw = strip_html(res.get("html") or "").strip()
            # Filter trivial noise pages (copyright, title page, short notices)
            if len(raw) < 80 and any(k in raw.lower() for k in ("copyright", "published", "table of contents")):
                continue
            snippets.append(raw[:1500])
            total_len += len(raw)
            if total_len >= 3500:
                break
        except Exception as e:
            logger.debug("Failed reading chapter %s for book %s: %s", ch_idx, book_row["id"], e)
            continue

    return "\n\n---\n\n".join(snippets)[:4000]


def _parse_llm_json(raw_text: str) -> Dict[str, Any]:
    """Parse JSON reply from LLM, stripping markdown code fences if present."""
    clean = raw_text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean)
        clean = re.sub(r"\s*```$", "", clean)
    return json.loads(clean)


def _call_llm_enrichment(book_row: Any, sample_text: str, feature: str = "refresh") -> Dict[str, Any]:
    """Request structured metadata re-generation from DeepSeek."""
    toc = " | ".join((book_row["toc_text"] or "").split(" | ")[:35])[:1200]
    user_prompt = (
        f"【当前库内书名】：{book_row['title']}\n"
        f"【作者】：{book_row['author']}\n"
        f"【ISBN】：{book_row['isbn'] or '未知'}\n"
        f"【原始文件名】：{os.path.basename(book_row['primary_path'] or '')}\n"  # name only: the full path stays local
        f"【章节目录 (TOC)】：\n{toc or '无目录记录'}\n\n"
        f"【真实正文摘录】：\n{sample_text or '未提取到正文切片'}\n"
    )

    resp_text, _ = ds.chat(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        json_mode=True,
        max_tokens=1200,
        timeout=120.0,
        feature=feature,
    )
    data = _parse_llm_json(resp_text)
    return _sanitize_llm_response(data, fallback_row=book_row)


def _preserve_bilingual_title(proposed_title: str, fallback_title: str) -> str:
    """Ensure standard '中文书名 (English Title)' format is preserved if original title had an English title."""
    proposed = proposed_title.strip()
    if not proposed:
        return fallback_title

    # Check if fallback_title had English title in parentheses e.g. '组织变革 (Organizational Change)'
    m_en = re.search(r"[\(（]([A-Za-z0-9\s,.:'’\-&/]{2,80})[\)）]\s*$", fallback_title)
    if m_en:
        en_part = m_en.group(1).strip()
        has_en_paren = bool(re.search(r"[\(（][A-Za-z0-9\s,.:'’\-&/]{2,80}[\)）]", proposed))
        if not has_en_paren:
            zh_part = re.sub(r"[\(（][^\(\)（）]*[\)）]$", "", proposed).strip() or proposed
            if zh_part.lower() != en_part.lower():
                return f"{zh_part} ({en_part})"
    return proposed


def _sanitize_llm_response(data: Dict[str, Any], fallback_row: Any) -> Dict[str, Any]:
    """Validate and clamp LLM output fields against allowed taxonomy boundaries."""
    category = data.get("category")
    if category not in CATEGORIES:
        category = fallback_row["category"] if fallback_row["category"] in CATEGORIES else "哲学宗教与通识"

    level = data.get("level") if data.get("level") in LEVELS else "普及"
    kind = data.get("kind") if data.get("kind") in KINDS else "学术理论"
    raw_clean = str(data.get("clean_title") or fallback_row["title"]).strip()
    raw_clean = re.sub(r"^[《「【\s]+|[》」】\s]+$", "", raw_clean)[:120]
    clean_title = _preserve_bilingual_title(raw_clean, str(fallback_row["title"]))
    summary = str(data.get("summary") or fallback_row["summary"] or "").strip()[:800]
    core_pts = [str(p).strip() for p in (data.get("core_points") or []) if str(p).strip()][:5]
    keywords = [str(k).strip() for k in (data.get("keywords") or []) if str(k).strip()][:10]

    return {
        "clean_title": clean_title or fallback_row["title"],
        "category": category,
        "summary": summary,
        "core_points": core_pts,
        "target_audience": str(data.get("target_audience") or "").strip()[:150],
        "keywords": keywords,
        "level": level,
        "kind": kind,
        "subfield": str(data.get("subfield") or "").strip()[:20],
    }


def remove_stale_hardlink(old_path: str) -> None:
    """Safely unlink previous hardlink and prune empty parent folder if under Library."""
    if not (old_path and os.path.exists(old_path) and is_path_within(old_path, LIBRARY_DIR)):
        return
    try:
        os.unlink(old_path)
        old_dir = Path(old_path).parent
        if old_dir.exists() and not any(old_dir.iterdir()):
            old_dir.rmdir()
    except OSError as e:
        logger.debug("Failed cleaning old hardlink %s: %s", old_path, e)


def relocate_archived_hardlink(conn, book_row: Any, new_title: str) -> Tuple[str, str]:
    """Link the book under its new title. Returns (archived_path, stale_path); the caller removes stale_path
    only after the database commit, so a failed write never leaves the book without its Library link."""
    old_archived = book_row["archived_path"] or ""
    src_path = book_row["primary_path"] or ""
    if not (old_archived and src_path and os.path.exists(src_path)):
        return old_archived, ""

    target = ArchiveTarget(
        title=new_title,
        ext=book_row["primary_format"] or "epub",
        author=book_row["author"] or "未知作者",
        volume_part=book_row["volume_part"] or "",
        edition_tag=book_row["edition_tag"] or "",
    )
    new_archived = safe_hardlink_to_library(src_path, target)
    # safe_hardlink_to_library returns the source path when linking failed: keep the existing link then
    if not new_archived or new_archived in (src_path, old_archived):
        return old_archived, ""

    conn.execute(
        "UPDATE book_files SET archived_path = ? WHERE book_id = ? AND is_primary = 1",
        (new_archived, book_row["id"]),
    )
    return new_archived, old_archived


def title_needing_confirmation(current_title: str, proposed_title: str) -> str:
    """The AI title the user must confirm, or '' when it only drops marketing text (then it is display-only)."""
    proposed = (proposed_title or "").strip()
    if not proposed or proposed == current_title or enrich_ai.is_deletion_of(proposed, current_title):
        return ""
    return proposed


def _update_books_metadata(conn, book_row: Any, enriched: Dict[str, Any]) -> None:
    """Writes the synopsis and taxonomy; the title stays: a renamed title needs the user's confirmation."""
    pinyin_tokens = build_cjk_pinyin_search_tokens(
        f"{book_row['title']} {book_row['author'] or ''} {enriched['category']} "
        f"{enriched['summary'][:SEARCH_TOKEN_SUMMARY_CHARS]}"
    )
    conn.execute(
        """
        UPDATE books SET
            category = ?, summary = ?,
            summary_source = 'ai_refreshed', core_points = ?,
            target_audience = ?, pinyin_search = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            enriched["category"], enriched["summary"],
            json.dumps(enriched["core_points"], ensure_ascii=False),
            enriched["target_audience"], pinyin_tokens, book_row["id"],
        ),
    )


def _upsert_ai_metadata(conn, book_id: int, enriched: Dict[str, Any]) -> None:
    curr_row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    in_hash = enrich_ai.input_hash(enrich_ai.book_input(curr_row))
    conn.execute(
        """
        INSERT INTO book_ai_meta
            (book_id, clean_title, keywords, level, kind, ai_category, subfield, input_hash, model, version, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(book_id) DO UPDATE SET
            input_hash = excluded.input_hash,
            clean_title = excluded.clean_title,
            keywords = excluded.keywords,
            level = excluded.level,
            kind = excluded.kind,
            ai_category = excluded.ai_category,
            subfield = excluded.subfield,
            model = excluded.model,
            version = excluded.version,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            book_id, enriched["clean_title"], json.dumps(enriched["keywords"], ensure_ascii=False),
            enriched["level"], enriched["kind"], enriched["category"], enriched["subfield"],
            in_hash, ds.FLASH_MODEL, enrich_ai.VERSION,
        ),
    )


def _channel_cover_or_none(enricher: Any, channel: Any, row: Any) -> Optional[str]:
    from enrichers.scheduler import _download_channel_cover
    lang = (row["language"] or "zh-CN").lower()
    res = enricher._attempt_channel_fetch(channel, row, lang)
    if not (res and res.status == "success" and res.cover_url):
        return None
    return _download_channel_cover(res.source, res.external_id, res.cover_url)


def _first_cover_path(row: Any) -> Optional[str]:
    """Downloaded cover of the first channel that knows this book, or None."""
    from enrichers.scheduler import MultiSourceSlowEnricher

    enricher = MultiSourceSlowEnricher()
    for channel in enricher._select_channel_order((row["language"] or "zh-CN").lower()):
        cover = _channel_cover_or_none(enricher, channel, row)
        if cover:
            return cover
    return None


def _fetch_missing_cover(bid: int, row: Any) -> None:
    """Fetch a cover for a book that has none; writes cover_path only.

    The full enrich_book_row would also add the rating bonus to classic_score again on every refresh and may
    replace a short summary with a description from a mismatched book, so only the cover is taken here.
    """
    try:
        cover = _first_cover_path(row)
    except Exception:
        logger.warning("cover fetch failed for book %s", bid, exc_info=True)
        return
    if not cover:
        return
    conn = get_conn()
    try:
        conn.execute("UPDATE books SET cover_path = ? WHERE id = ? AND COALESCE(cover_path, '') = ''", (cover, bid))
        conn.commit()
    finally:
        conn.close()


def _start_cover_fetch(bid: int, row: Any) -> None:
    """Covers come from external sites (seconds each): fetched in the background so the refresh request returns."""
    if (row["cover_path"] or "").strip():
        return
    threading.Thread(target=_fetch_missing_cover, args=(bid, row), daemon=True, name=f"cover-{bid}").start()


def refresh_book_ai_metadata(
    book_id: int,
    feature: str = "refresh",
    reindex_fn: Optional[Callable[[int], None]] = None,
) -> Dict[str, Any]:
    """Analyze ground-truth content, refresh LLM metadata & summary, and sync vectors."""
    conn = get_conn()
    try:
        db_row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        if not db_row:
            raise BookNotFoundError(f"book {book_id} not found")
        row = dict(db_row)
    finally:
        conn.close()

    sample_text = _extract_sample_content(row)
    if not sample_text.strip():
        raise NoReadableTextError(f"book {book_id} has no readable text; metadata left unchanged")
    enriched = _call_llm_enrichment(row, sample_text, feature=feature)

    enriched["proposed_title"] = title_needing_confirmation(row["title"], enriched["clean_title"])
    if enriched["proposed_title"]:
        enriched["clean_title"] = row["title"]
    with write_tx(conn_factory=get_conn) as conn:
        _update_books_metadata(conn, row, enriched)
        _upsert_ai_metadata(conn, book_id, enriched)

    _start_cover_fetch(book_id, row)
    logger.info("book %s refreshed; previous title=%r category=%r", book_id, row["title"], row["category"])
    enriched["previous"] = {"title": row["title"], "category": row["category"], "summary": row["summary"]}

    if reindex_fn is not None:
        reindex_fn(book_id)
    else:
        spawn_reindex({book_id})
    try:
        from catalog_sync import enqueue_catalog_sync

        enqueue_catalog_sync()
    except Exception:
        logger.debug("Catalog sync enqueue failed after book %s refresh", book_id, exc_info=True)
    logger.info("Successfully refreshed AI metadata for book %s: %s", book_id, row["title"])
    return enriched
