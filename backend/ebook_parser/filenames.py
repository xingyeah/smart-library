import hashlib
import html
import os
import re
from pathlib import Path
from typing import Dict, Optional, Tuple

SPAM_PATTERNS = [
    r"【[^】]*(?:公众号|关注|下载|微信|书库|整理|分享|免费|电子书|校对|精校|完美|全本|更多)[^】]*】",
    r"\[[^\]]*(?:z-lib|zlib|anna|sobooks|kindle|epub|mobi|azw3|公众号|下载|分享)[^\]]*\]",
    r"\([^\)]*(?:z-library|z-lib\.org|annas-archive|sobooks|公众号|下载)[^\)]*\)",
    r"（[^）]*(?:z-library|公众号|下载|全本|精校版|完美版)[^）]*）",
    r"(?:www\.|https?://)[a-zA-Z0-9\.\-/]+",
]

VOLUME_REGEX = re.compile(
    r"(?:第\s*([0-9一二三四五六七八九十百]+)\s*[卷册部辑季篇]|"
    r"([上中下])\s*[册卷部]|"
    r"[\(（]([上中下1-9一二三四五六七八九十]+)[\)）]|"
    r"\b(?:vol(?:ume)?|part|book)\.?\s*([0-9ivx]+)\b|"
    r"(全\s*[0-9一二三四五六七八九十]+\s*[册卷部]))",
    re.IGNORECASE,
)

EDITION_REGEX = re.compile(
    r"(?:第\s*([0-9一二三四五六七八九十]+)\s*版|"
    r"\b([0-9]+(?:st|nd|rd|th))\s*ed(?:ition)?\b|"
    r"(修订版|增订版|典藏版|珍藏版|插图版|纪念版|彩色版|注释版|全译本|最新修订版))",
    re.IGNORECASE,
)

COUNTRY_PREFIX_REGEX = re.compile(
    r"^[\[\(【（]\s*([美英法德日俄苏奥意西印加澳荷波兰以色列古希腊古罗马清唐宋明民国][^\]\)】）]{0,4})\s*[\]\)】）]\s*"
)

HTML_TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")

CN_NUM_MAP = {
    "一": "1",
    "二": "2",
    "三": "3",
    "四": "4",
    "五": "5",
    "六": "6",
    "七": "7",
    "八": "8",
    "九": "9",
    "十": "10",
    "上": "上",
    "中": "中",
    "下": "下",
}


def compute_fast_file_hash(file_path: str) -> str:
    """Fast composite SHA256 hash using file size + first 64KB + middle 64KB + last 64KB."""
    try:
        size = os.path.getsize(file_path)
        h = hashlib.sha256()
        h.update(str(size).encode("utf-8"))
        with open(file_path, "rb") as f:
            h.update(f.read(65536))
            if size > 131072:
                f.seek(size // 2)
                h.update(f.read(65536))
            if size > 65536:
                f.seek(max(0, size - 65536))
                h.update(f.read(65536))
        return h.hexdigest()[:32]
    except Exception:
        return hashlib.md5(file_path.encode("utf-8", errors="ignore")).hexdigest()


def strip_html(raw_html: str) -> str:
    if not raw_html:
        return ""
    text = re.sub(r"<(?:script|style)[^>]*>.*?</(?:script|style)>", " ", raw_html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<(?:br|p|div|li|tr|h[1-6])[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = HTML_TAG_RE.sub("", text)
    text = html.unescape(text)
    lines = [WHITESPACE_RE.sub(" ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln and not re.match(r"^(?:更多好书|本书由|下载自|扫码关注)", ln)]
    return "\n".join(lines).strip()


def clean_spam_text(s: str) -> str:
    if not s:
        return ""
    res = s
    for pat in SPAM_PATTERNS:
        res = re.sub(pat, "", res, flags=re.IGNORECASE)
    res = res.replace("\u3000", " ").strip(" _-—·|")
    return res.strip()


def extract_volume_and_edition(text: str) -> Tuple[str, str]:
    if not text:
        return "", ""
    vol = ""
    m_vol = VOLUME_REGEX.search(text)
    if m_vol:
        raw_v = next((g for g in m_vol.groups() if g), "")
        vol = CN_NUM_MAP.get(raw_v, raw_v).lower()

    ed = ""
    m_ed = EDITION_REGEX.search(text)
    if m_ed:
        raw_e = next((g for g in m_ed.groups() if g), "")
        ed = CN_NUM_MAP.get(raw_e, raw_e).lower()

    return vol, ed


def normalize_for_dedup(title: str, author: str = "") -> Tuple[str, str]:
    """Normalize title and author to canonical keys while stripping subtitle noise."""
    t = clean_spam_text(title or "")
    t = re.sub(r"^[《「『【\[]+|[》」』】\]]+$", "", t)
    parts = re.split(r"[:：—\-~～\(\（\[]", t)
    if parts and len(parts[0].strip()) >= 2:
        t_core = parts[0].strip()
    else:
        t_core = t.strip()
    t_norm = re.sub(r"[^\w\u4e00-\u9fff]+", "", t_core).lower()
    if not t_norm:
        t_norm = re.sub(r"[^\w\u4e00-\u9fff]+", "", t).lower() or "unknown"

    a = clean_spam_text(author or "")
    a = COUNTRY_PREFIX_REGEX.sub("", a)
    a = re.sub(r"(?:著|编著|主编|作者|编|译|绘|等著|等编|校注)$", "", a.strip())
    a_parts = re.split(r"[,，;；/、&＆\s]+", a.strip())
    a_core = a_parts[0] if a_parts else a
    a_norm = re.sub(r"[^\w\u4e00-\u9fff]+", "", a_core).lower() or "未知作者"
    return t_norm, a_norm


def parse_bilingual_filename(raw_stem: str) -> Optional[Dict[str, str]]:
    s = re.sub(
        r"\s*[\(\[（【](?:z-library|z-lib|1lib|annas-archive|sobooks)[^)\]\）】]*[\)\]）】]\s*$",
        "",
        raw_stem,
        flags=re.IGNORECASE,
    ).strip()
    s = clean_spam_text(s)
    m_end = re.search(
        r"[\(（]((?:[^()（）]|[\(（][^()（）]*[\)）])*[\u4e00-\u9fff](?:[^()（）]|[\(（][^()（）]*[\)）])*)[\)）]\s*$",
        s,
    )
    if not m_end:
        return None
    zh_full = m_end.group(1).strip()
    prefix = s[: m_end.start()].strip()
    m_prev_cjk = re.search(r"[\(（]([^()（）]*[\u4e00-\u9fff][^()（）]*)[\)）]\s*$", prefix)
    prev_cjk_auth = ""
    if m_prev_cjk:
        prev_cjk_auth = m_prev_cjk.group(1).strip()
        prefix = prefix[: m_prev_cjk.start()].strip()

    if not re.search(r"[A-Za-z]{2,}", prefix) or re.search(r"[\u4e00-\u9fff]", prefix):
        return None
    en_words = re.findall(r"[A-Za-z]{2,}", prefix)
    if len(en_words) < 2 and len(prefix) < 8 and not re.search(r"\([^()]+\)\s*$", prefix):
        return None

    COMMON_EN = {
        "the", "and", "of", "to", "in", "for", "with", "on", "at", "from", "by", "about",
        "when", "every", "word", "words", "counts", "how", "what", "why", "who", "which",
        "life", "work", "mind", "minds", "good", "great", "book", "books", "make", "time",
        "day", "days", "art", "new", "world", "people", "man", "men", "human", "ai", "data"
    }
    words_lower = {w.lower() for w in re.findall(r"[A-Za-z]+", prefix)}
    if not (words_lower & COMMON_EN) and re.match(r"^(?:[A-Z][a-z]{1,4}\s+){5,}", prefix):
        return None
    if len(zh_full) <= 2 or re.match(r"^(?:中文版|中译本|全译本|修订版|第.+版|[美英法德日俄])$", zh_full):
        return None

    zh_full_clean = re.sub(r"\s*[\(（][^()（）]{2,12}[\)）]\s*$", "", zh_full).strip() or zh_full
    auth = ""
    prefix = re.sub(r"\s*\((?:[A-Za-z0-9\s]*Early Release|Draft|Edition|\d+th Edition)\)\s*", " ", prefix, flags=re.IGNORECASE).strip()

    m_auth_paren = re.search(r"\(([^()]+)\)\s*$", prefix)
    if m_auth_paren:
        cand = m_auth_paren.group(1).strip()
        if not VOLUME_REGEX.search(cand) and not EDITION_REGEX.search(cand):
            auth = cand
            prefix = prefix[: m_auth_paren.start()].strip()
    if not auth and prev_cjk_auth:
        auth = prev_cjk_auth

    m_by = re.search(r"\s+by\s+([A-Za-z\s\.,\-\'&]+)$", prefix, re.IGNORECASE)
    if m_by:
        if not auth:
            auth = m_by.group(1).strip()
        prefix = prefix[: m_by.start()].strip()

    if " - " in prefix and not auth:
        segs = [x.strip() for x in prefix.split(" - ") if x.strip()]
        if len(segs) == 2 and len(segs[1]) <= 35:
            prefix = segs[0]
            auth = segs[1]

    en_main = prefix.strip(" -_:,;")
    en_sub = ""
    m_en_sub = re.match(r"^([^:：;]{3,55})[:：;]\s*(.+)$", en_main)
    if m_en_sub:
        en_main = m_en_sub.group(1).strip()
        en_sub = m_en_sub.group(2).strip()

    zh_main = zh_full_clean
    zh_sub = ""
    m_zh_sub = re.match(r"^([^:：—]{2,35})(?:[:：]|——)\s*(.+)$", zh_full_clean)
    if m_zh_sub:
        zh_main = m_zh_sub.group(1).strip()
        zh_sub = m_zh_sub.group(2).strip()

    norm_en = re.sub(r"[^\w]+", "", en_main).lower()
    norm_zh = re.sub(r"[^\w]+", "", zh_main).lower()
    if norm_en == norm_zh or (norm_en and norm_en in norm_zh):
        bilingual_title = zh_main
    else:
        bilingual_title = f"{zh_main} ({en_main})" if en_main else zh_main
    combined_sub = " · ".join(x for x in (zh_sub, en_sub) if x)
    return {
        "title": bilingual_title,
        "subtitle": combined_sub,
        "author": auth,
        "zh_title": zh_full,
        "en_title": en_main,
    }


def parse_filename_metadata(file_path: str) -> Dict[str, str]:
    """Extract candidate title, subtitle, author, volume, and edition from filename."""
    raw_stem = Path(file_path).stem
    author = ""
    subtitle = ""

    bi = parse_bilingual_filename(raw_stem)
    if bi:
        vol, ed = extract_volume_and_edition(raw_stem)
        return {
            "title": bi["title"],
            "subtitle": bi["subtitle"],
            "author": bi["author"],
            "volume_part": vol,
            "edition_tag": ed,
            "is_bilingual": "1",
        }

    m_zlib = re.search(
        r"\(([^()]{2,45})\)\s*\((?:z-library|z-lib\.org|1lib\.sk|z-lib\.sk|annas-archive)[^()]*\)\s*$",
        raw_stem,
        re.IGNORECASE,
    )
    if m_zlib:
        cand_auth = m_zlib.group(1).strip()
        if not re.search(r"(?:出版社|出版|系列|丛书|\d{4})", cand_auth):
            author = cand_auth
            raw_stem = raw_stem[: m_zlib.start()].strip()

    stem = clean_spam_text(raw_stem)
    stem = re.sub(r"[\s_\-]+(?:\d{6,}|\(\d+\))$", "", stem)

    vol, ed = extract_volume_and_edition(stem)
    title = stem

    m_by_en = re.search(r"^([A-Za-z0-9\s,:;\-\'\"\.\?!&/]+?)\s+by\s+([A-Za-z\s\.,\-\'&]+)$", title, re.IGNORECASE)
    if m_by_en:
        title = m_by_en.group(1).strip()
        author = author or m_by_en.group(2).strip()

    m_book = re.match(r"^《([^》]+)》\s*(?:作者[:：]?\s*)?(.+)?$", stem)
    if m_book:
        title = m_book.group(1).strip()
        rem = (m_book.group(2) or "").strip(" -_—")
        if rem and len(rem) <= 25 and not author:
            author = rem
    elif " - " in stem and not author:
        segs = [s.strip() for s in stem.split(" - ") if s.strip()]
        if len(segs) >= 2:
            if COUNTRY_PREFIX_REGEX.match(segs[0]) or (len(segs[0]) <= 8 and len(segs[1]) > len(segs[0])):
                if COUNTRY_PREFIX_REGEX.match(segs[0]):
                    author = author or segs[0]
                    title = segs[1]
                else:
                    title = segs[0]
                    author = author or segs[-1]
            else:
                title = segs[0]
                author = author or segs[-1]

    m_trail_paren = re.search(r"\(([^()]{2,50})\)\s*$", title)
    if m_trail_paren:
        inside = m_trail_paren.group(1).strip()
        prefix = title[: m_trail_paren.start()].strip()
        if re.search(r"[:：——]", inside) or (len(prefix) <= 6 and not re.search(r"[\u4e00-\u9fff]", prefix)):
            title = inside
        elif not author and not VOLUME_REGEX.search(inside) and not EDITION_REGEX.search(inside) and len(inside) <= 25:
            author = inside
            title = prefix

    m_sub = re.match(r"^([^:：]{2,35})[:：]\s*(.+)$", title)
    if m_sub:
        title = m_sub.group(1).strip()
        subtitle = m_sub.group(2).strip()

    return {
        "title": title.strip() or Path(file_path).stem,
        "subtitle": subtitle.strip(),
        "author": author.strip(),
        "volume_part": vol,
        "edition_tag": ed,
        "is_bilingual": "",
    }


_CJK = re.compile(r"[一-鿿]")
_TRAILING_CJK_GROUP = re.compile(r"\s*[(（][^()（）]*[一-鿿][^()（）]*[)）]\s*$")
# '(Bent Flyvbjerg, Dan Gardner)' / '(A and B)': several authors left on the title; '(Revised Edition)' has no separator
_TRAILING_AUTHOR_LIST = re.compile(r"\s*\((?![^()]*\d)[^()]*(?:,| and | & )[^()]*\)\s*$")
_LEADING_NUMBER = re.compile(r"^\d{1,4}[.\s_-]+(?=[A-Za-z])")


def english_title_from_path(file_path: str) -> str:
    """Real English title from the file name: drops the appended '(中文名)', a leading catalogue number and '(Author)'."""
    stem = os.path.splitext(os.path.basename(file_path))[0]
    while _TRAILING_CJK_GROUP.search(stem):
        stem = _TRAILING_CJK_GROUP.sub("", stem)
    stem = _LEADING_NUMBER.sub("", stem).strip()
    if not stem or _CJK.search(stem):
        return ""
    title = (parse_filename_metadata(f"{stem}.x").get("title") or "").strip()
    title = _TRAILING_AUTHOR_LIST.sub("", title).strip()
    return "" if _CJK.search(title) else title
