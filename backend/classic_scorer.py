import logging
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from config import COVERS_DIR
from database import get_conn, init_db
from parser import detect_book_language

# '丛书' names a publisher's series, so it marked single volumes ('物种起源 (汉译世界学术名著丛书)') as box sets.
# The AI's book type alone is not enough: it describes the content, so '奥威尔小说全集（套装共6册）' is typed 小说文学.
ANTHOLOGY_KIND = "套装合集"
ANTHOLOGY_REGEX = re.compile(
    r"(?:套装|合集|全集|作品集|必读系列|好书系列|精选集|文集|"
    r"共[0-9一二三四五六七八九十百]+[册部卷辑]|"
    r"[（\(]\s*(?:套装\s*)?[0-9一二三四五六七八九十百]+\s*[册部卷辑]\s*[）\)]|"
    r"\b\d+\s*books?\s*in\s*1\b|\bbox\s*set\b|\bomnibus\b)",
    re.IGNORECASE,
)

EDITION_NUM_REGEX = re.compile(
    r"(?:第\s*([0-9一二三四五六七八九十]+)\s*版|"
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+edition\b|"
    r"\b(\d{1,2})e\b)",
    re.IGNORECASE,
)

ANNIVERSARY_REGEX = re.compile(
    r"(?:(\d{1,3})\s*周年|(\d{1,3})(?:th|st|nd|rd)\s+anniversary|"
    r"纪念版|珍藏版|典藏版|白金版|精装典藏|汉译世界学术名著|牛津通识读本|企鹅经典)",
    re.IGNORECASE,
)

TIER1_CANON_MARKERS = [
    "之父", "奠基", "开山", "圣经", "诺贝尔", "图灵奖", "普利策", "菲尔兹",
    "茅盾文学奖", "雨果奖", "星云奖", "里程碑", "传世", "不朽", "扛鼎",
    "黄金圈", "定位理论", "影响力", "科特勒", "德鲁克", "卡尼曼", "芒格",
    "巴菲特", "高德纳", "费曼", "达尔文", "亚当·斯密", "凯恩斯", "熊彼特",
    "西奥迪尼", "特劳特", "奥格威", "霍普金斯", "克劳塞维茨", "马基雅维利",
]

TIER2_CANON_MARKERS = [
    "经典", "代表作", "大师", "先驱", "泰斗", "权威", "教科书", "必读",
    "畅销", "长销", "风靡", "享誉", "盛誉", "名著", "名作", "力作",
    "seminal", "classic", "bestseller", "definitive", "masterpiece",
]

CN_NUM_MAP = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "十一": 11, "十二": 12, "十三": 13, "十四": 14, "十五": 15,
    "十六": 16, "十七": 17, "十八": 18, "十九": 19, "二十": 20,
}


def _parse_cn_or_int(val: str) -> int:
    if not val:
        return 0
    val = val.strip()
    if val.isdigit():
        return int(val)
    return CN_NUM_MAP.get(val, 0)


def douban_bonus(rating: float, votes: int) -> float:
    """Classic-score points from a Douban rating (-10 to +22); one rule for the full rescore and the slow enricher."""
    if rating < 5.0:
        return 0.0
    vote_weight = min(1.0, math.log10(max(20, votes or 150)) / 4.2)
    return (rating - 7.0) * 8.5 * vote_weight


def compute_book_classic_Signals(
    book: Dict[str, Any],
    file_count: int,
    source_count: int,
    same_title_count: int,
    author_book_count: int,
) -> Tuple[float, int]:
    """
    Compute (classic_score, is_anthology) using multi-dimensional intrinsic & bibliographic signals.
    Returns classic_score in [0.0, 100.0] and is_anthology in {0, 1}.
    """
    title = (book.get("title") or "").strip()
    subtitle = (book.get("subtitle") or "").strip()
    author = (book.get("author") or "").strip()
    translator = (book.get("translator") or "").strip()
    edition_tag = (book.get("edition_tag") or "").strip()
    summary = (book.get("summary") or "").strip()
    series = (book.get("series") or "").strip()
    pub_year = (book.get("pub_year") or "").strip()
    douban_rating = float(book.get("douban_rating") or 0.0)
    douban_votes = int(book.get("douban_votes") or 0)

    title_sub_ed = f"{title} {subtitle} {edition_tag} {series}"
    full_meta = f"{title_sub_ed} {author} {summary}"
    full_low = full_meta.lower()

    # 1. Anthology / Box-set detection
    is_anthology = is_anthology_book(title, subtitle, book.get("ai_kind") or "")

    score = 15.0  # Base score

    # 2. Multi-file / Multi-source / Multi-edition collection signal (0 - 22 pts)
    extra_files = max(0, file_count - 1)
    extra_sources = max(0, source_count - 1)
    extra_editions = max(0, same_title_count - 1)
    collection_pts = min(
        22.0,
        extra_files * 6.5 + extra_sources * 5.5 + extra_editions * 4.5,
    )
    score += collection_pts

    # 3. Edition & Revision Longevity Signal (0 - 24 pts)
    max_ed = 0
    for m in EDITION_NUM_REGEX.finditer(title_sub_ed + " " + summary[:120]):
        ed_n = _parse_cn_or_int(m.group(1) or m.group(2) or m.group(3) or "")
        if 2 <= ed_n <= 30:
            max_ed = max(max_ed, ed_n)

    if max_ed >= 2:
        score += min(24.0, 7.0 + max_ed * 1.35)

    m_anniv = ANNIVERSARY_REGEX.search(title_sub_ed + " " + summary[:120])
    if m_anniv:
        yrs = _parse_cn_or_int(m_anniv.group(1) or m_anniv.group(2) or "")
        score += min(18.0, 10.0 + (yrs * 0.2 if yrs > 0 else 4.0))

    # 4. Canonical Authority & Classic Impact Markers (0 - 26 pts)
    t1_hits = sum(1 for kw in TIER1_CANON_MARKERS if kw.lower() in full_low)
    t2_hits = sum(1 for kw in TIER2_CANON_MARKERS if kw.lower() in full_low)
    canon_pts = min(26.0, t1_hits * 9.5 + t2_hits * 4.2)
    score += canon_pts

    # 5. Author Stature & Cross-Language Translation Signal (0 - 16 pts)
    if author and author != "未知作者":
        if author_book_count >= 2:
            score += min(10.0, math.log2(author_book_count) * 3.2)
        # Bilingual title (e.g. "品牌推动者 (Brand Mover)") or translated work
        has_bilingual_title = bool(re.search(r"[\u4e00-\u9fff].*\([A-Za-z0-9\s:,\'\-&]{4,}\)", title))
        has_foreign_nation = bool(re.search(r"^[\[【\(（][美英日法德俄奥意加澳印以荷瑞][\]】\)）]", author))
        if translator or has_bilingual_title or has_foreign_nation:
            score += 5.5

    # 6. Publication Year Endurance Signal (0 - 6 pts)
    m_yr = re.search(r"\b(19\d{2}|20[0-2]\d)\b", pub_year)
    if m_yr:
        yr = int(m_yr.group(1))
        if 1950 <= yr <= 2018:
            score += min(6.0, (2025 - yr) * 0.18)
        elif 2019 <= yr <= 2026:
            score += 2.5

    # 7. Douban Rating & Votes Signal (when available, -10 to +22 pts)
    score += douban_bonus(douban_rating, douban_votes)

    # 8. Anthology damping for single-book precision
    if is_anthology:
        score *= 0.68

    return round(min(99.5, max(10.0, score)), 2), is_anthology


def is_anthology_book(title: str, subtitle: str, ai_kind: str) -> int:
    """1 for a box set or collection of separate works, else 0."""
    if ai_kind == ANTHOLOGY_KIND:
        return 1
    return 1 if ANTHOLOGY_REGEX.search(f"{title} {subtitle}") else 0


def backfill_languages_and_classic_scores(only_ids: Optional[set] = None) -> Dict[str, Any]:
    """
    1. Fix all books where language NOT IN ('zh-CN', 'zh-TW', 'en').
    2. Compute and persist classic_score and is_anthology for all 7,037 books.
    """
    t0 = time.time()
    init_db()
    conn = get_conn()
    try:
        file_rows = conn.execute(
            "SELECT book_id, original_path, source_name, format FROM book_files"
        ).fetchall()
        files_by_book: Dict[int, List[sqlite3.Row]] = {}
        for fr in file_rows:
            files_by_book.setdefault(int(fr["book_id"]), []).append(fr)

        all_rows = conn.execute(
            "SELECT b.*, m.kind AS ai_kind FROM books b LEFT JOIN book_ai_meta m ON m.book_id = b.id"
        ).fetchall()

        # Count norm_title and norm_author frequencies across the whole library
        title_counts: Dict[str, int] = {}
        author_counts: Dict[str, int] = {}
        for r in all_rows:
            nt = (r["norm_title"] or "").strip()
            na = (r["norm_author"] or "").strip()
            if nt:
                title_counts[nt] = title_counts.get(nt, 0) + 1
            if na and na != "未知作者":
                author_counts[na] = author_counts.get(na, 0) + 1

        rows = all_rows
        if only_ids is not None:
            rows = [r for r in all_rows if int(r["id"]) in only_ids]

        lang_fixed = 0
        anthology_count = 0
        updates: List[Tuple[str, float, int, int]] = []

        for r in rows:
            b = dict(r)
            bid = int(b["id"])
            b_files = files_by_book.get(bid, [])
            bnames = [os.path.basename(f["original_path"]) for f in b_files if f["original_path"]]
            sources = {f["source_name"] for f in b_files if f["source_name"]}

            lang = (b.get("language") or "").strip()
            if lang not in ("zh-CN", "zh-TW", "en"):
                lang = detect_book_language(
                    bnames=bnames,
                    title=b.get("title") or "",
                    subtitle=b.get("subtitle") or "",
                    author=b.get("author") or "",
                    translator=b.get("translator") or "",
                    toc_text=b.get("toc_text") or "",
                    primary_path=b.get("primary_path") or "",
                )
                lang_fixed += 1

            nt = (b.get("norm_title") or "").strip()
            na = (b.get("norm_author") or "").strip()
            c_score, is_anth = compute_book_classic_Signals(
                book=b,
                file_count=max(1, len(b_files)),
                source_count=max(1, len(sources)),
                same_title_count=title_counts.get(nt, 1),
                author_book_count=author_counts.get(na, 1) if na != "未知作者" else 1,
            )
            if is_anth:
                anthology_count += 1
            updates.append((lang, c_score, is_anth, bid))

        conn.executemany(
            "UPDATE books SET language = ?, classic_score = ?, is_anthology = ? WHERE id = ?",
            updates,
        )
        conn.commit()
        elapsed = round(time.time() - t0, 2)
        return {
            "total_books": len(rows),
            "lang_fixed": lang_fixed,
            "anthologies_marked": anthology_count,
            "elapsed_sec": elapsed,
        }
    finally:
        conn.close()


# Background Slow Douban Rating Enricher Daemon
_douban_thread: Optional[threading.Thread] = None
_douban_stop = threading.Event()


def _try_download_douban_cover(subject_id: str, pic_url: str) -> Optional[str]:
    """Download official Douban cover and return relative web path."""
    if not (subject_id and pic_url):
        return None
    fname = f"douban_{subject_id}.jpg"
    dest = COVERS_DIR / fname
    if dest.exists() and dest.stat().st_size > 0:
        return f"/covers/{fname}"
    try:
        req = urllib.request.Request(pic_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6.0) as resp:
            content = resp.read()
            if content.startswith((b"\xff\xd8\xff", b"\x89PNG", b"RIFF")):
                dest.write_bytes(content)
                return f"/covers/{fname}"
    except Exception:
        # Cover download failure is non-fatal, fallback to text placeholder
        logging.getLogger(__name__).debug("cover download failed; card falls back to the text placeholder", exc_info=True)
    return None


def _fetch_douban_rating_for_book(title: str, author: str, isbn: str) -> Optional[Tuple[float, int, str, str]]:
    """Query Douban via DoubanEnricher (with title/author verification) to retrieve (rating, votes, douban_id, pic_url)."""
    from enrichers.base import BookQuery, search_title
    from enrichers.douban import DoubanEnricher

    query = BookQuery(
        isbn=isbn or "",
        title=search_title(title or ""),
        author=author or "",
    )
    enricher = DoubanEnricher()
    enricher.timeout_sec = 6.0
    meta = enricher.fetch(query)
    if not meta or meta.status != "success" or meta.rating <= 0:
        return None
    return (float(meta.rating), int(meta.votes or 0), str(meta.external_id), str(meta.cover_url or ""))


def _enrich_one_douban_book(row: Any) -> bool:
    """Fetch and update Douban score for candidate row, backfilling missing cover."""
    bid = int(row["id"])
    try:
        res = _fetch_douban_rating_for_book(row["title"], row["author"], row["isbn"])
    except OSError:
        return False

    conn = get_conn()
    try:
        if not res:
            conn.execute("UPDATE books SET douban_rating = -1.0 WHERE id = ?", (bid,))
            conn.commit()
            return True

        rating, votes, db_id, pic_url = res
        new_cscore = round(min(99.5, max(10.0, float(row["classic_score"] or 50.0) + douban_bonus(rating, votes))), 2)
        cpath = row["cover_path"] or ""
        if not cpath:
            cpath = _try_download_douban_cover(db_id, pic_url) or ""

        conn.execute(
            "UPDATE books SET douban_rating = ?, douban_votes = ?, douban_id = ?, "
            "classic_score = ?, cover_path = ? WHERE id = ?",
            (rating, votes, db_id, new_cscore, cpath, bid),
        )
        conn.commit()
    finally:
        conn.close()
    return True


def start_background_douban_enricher(interval_sec: float = 12.0) -> None:
    """Start multi-source background slow enricher daemon (backward compatibility bridge)."""
    from enrichers import start_background_slow_enricher

    start_background_slow_enricher(interval_sec=interval_sec)


if __name__ == "__main__":
    print(backfill_languages_and_classic_scores())
