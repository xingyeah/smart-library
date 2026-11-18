"""DeepSeek rerank of search results: scores the top N books 0-10 against the query, cached in SQLite."""

import hashlib
import json
import logging
from typing import Any, Dict, List, Tuple

import deepseek_client as ds
from database import get_conn
from enrich_ai import VERSION as ENRICH_VERSION

logger = logging.getLogger("ai_search")

RERANK_SYSTEM = (
    "你是图书检索的相关性评审。给出读者的查询和候选书列表，为每本书打 0-10 分：10=正是读者想要的（主题、难度、类型、作者、"
    "排除条件全部符合），5=主题相关但不完全符合要求，0=不相关或违背要求（如要入门却是专业教材，要技术原理却是投资指南）。"
    '只依据给出的信息判断。只输出 json，格式：{"scores": {"<书的id>": 8, "<书的id>": 2}}'
)


def _cache_key(query: str, ids: List[int]) -> str:
    # Enrichment version is in the key: re-tagged books invalidate old scores
    return hashlib.sha1(f"v{ENRICH_VERSION}|{query.strip()}|{','.join(map(str, ids))}".encode("utf-8")).hexdigest()


def _cached(conn: Any, key: str) -> Dict[str, int]:
    conn.execute("CREATE TABLE IF NOT EXISTS ai_rerank_cache (key TEXT PRIMARY KEY, scores TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    row = conn.execute("SELECT scores FROM ai_rerank_cache WHERE key = ?", (key,)).fetchone()
    return json.loads(row["scores"]) if row else {}


def score_books(query: str, items: List[Dict[str, Any]], feature: str = "search") -> Tuple[Dict[int, int], str]:
    """({book_id: 0-10}, reason). Empty scores on failure so callers keep the standard order;
    reason is "" on success, else "nokey" / "cap" / "error"."""
    if not ds.api_key():
        return {}, "nokey"
    ids = [int(b["id"]) for b in items]
    key = _cache_key(query, ids)
    conn = get_conn()
    try:
        cached = _cached(conn, key)
        if cached:
            return {int(k): v for k, v in cached.items()}, ""
        lines = "\n".join(
            f"[{b['id']}] 《{(b.get('clean_title') or b.get('title') or '')[:60]}》 | {(b.get('author') or '')[:30]} | "
            f"{b.get('ai_level') or ''}/{b.get('ai_kind') or ''} | {(b.get('summary') or '')[:150]}"
            for b in items
        )
        try:
            out, _ = ds.chat_json(
                [{"role": "system", "content": RERANK_SYSTEM}, {"role": "user", "content": f"读者查询：{query}\n\n候选书：\n{lines}"}],
                max_tokens=2000, timeout=10, feature=feature,
            )
        except ds.DailyCapReached:
            return {}, "cap"
        except Exception as e:
            logger.warning(f"AI rerank failed, keeping standard order: {e}")
            return {}, "error"
        valid = set(ids)
        scores = {}
        for k, v in (out.get("scores") or {}).items():  # trust boundary: known ids, integer 0-10
            try:
                bid, sc = int(k), int(v)
            except (TypeError, ValueError):
                continue
            if bid in valid and 0 <= sc <= 10:
                scores[bid] = sc
        if scores:
            conn.execute("INSERT OR REPLACE INTO ai_rerank_cache (key, scores) VALUES (?, ?)", (key, json.dumps(scores)))
            conn.commit()
        return scores, "" if scores else "error"
    finally:
        conn.close()


def rerank(
    query: str, items: List[Dict[str, Any]], top_n: int = 20, feature: str = "search"
) -> Tuple[List[Dict[str, Any]], str]:
    """(items, reason): AI score first, standard order as tie-break; books beyond top_n keep their place after the head."""
    head, tail = items[:top_n], items[top_n:]
    scores, reason = score_books(query, head, feature) if head else ({}, "")
    if not scores:
        return items, reason
    for b in head:
        b["ai_score"] = scores.get(int(b["id"]))
    order = sorted(range(len(head)), key=lambda i: (-(scores.get(int(head[i]["id"]), -1)), i))
    return [head[i] for i in order] + tail, ""


PICK_SYSTEM = (
    "你是私人图书馆的选书顾问。根据读者画像（兴趣描述和他标记过的书），从候选书中挑出最适合他接下来读的 {n} 本，"
    "按推荐程度从高到低排列，兼顾主题多样性，不要挑同一本书的不同版本。为每本写一句中文推荐理由（不超过 40 字），"
    "要具体联系读者的兴趣或他标记过的书，不要空话。只能从候选里选。"
    '只输出 json，格式：{{"picks": [{{"id": <书的id>, "reason": "..."}}]}}'
)
_MARK_LABEL = {"want": "想读", "reading": "在读", "read": "已读"}


def interest_profile(interest_prompt: str) -> str:
    """Reader profile for DeepSeek: the interest text plus up to 40 most recently marked books."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT title, reading_status, is_favorite, user_rating FROM books "
            "WHERE is_favorite = 1 OR COALESCE(reading_status, 'unread') NOT IN ('unread', '') OR COALESCE(user_rating, 0) > 0 "
            "ORDER BY updated_at DESC LIMIT 40"
        ).fetchall()
    finally:
        conn.close()
    marks = []
    for r in rows:
        tags = [t for t in (_MARK_LABEL.get(r["reading_status"] or ""), "喜欢" if r["is_favorite"] else "",
                            f"{r['user_rating']}星" if r["user_rating"] else "") if t]
        marks.append(f"《{(r['title'] or '')[:40]}》({'、'.join(tags)})")
    return f"兴趣描述：{interest_prompt or '（未填写）'}\n标记过的书：{'；'.join(marks) or '（暂无）'}"


def pick_books(
    profile: str, items: List[Dict[str, Any]], top_k: int = 10, feature: str = "recommend"
) -> Tuple[List[Tuple[int, str]], str]:
    """([(book_id, reason)], status) in DeepSeek's order; status is "" on success, else "nokey" / "cap" / "error"."""
    if not ds.api_key():
        return [], "nokey"
    ids = [int(b["id"]) for b in items]
    key = _cache_key(f"pick{top_k}|{profile}", ids)
    conn = get_conn()
    try:
        cached = _cached(conn, key)
        if cached:
            return [(int(i), r) for i, r in cached["picks"]], ""
        lines = "\n".join(
            f"[{b['id']}] 《{(b.get('clean_title') or b.get('title') or '')[:60]}》 | {(b.get('author') or '')[:30]} | "
            f"{b.get('category') or ''} | {(b.get('summary') or '')[:120]}"
            for b in items
        )
        try:
            out, _ = ds.chat_json(
                [{"role": "system", "content": PICK_SYSTEM.format(n=top_k)},
                 {"role": "user", "content": f"{profile}\n\n候选书：\n{lines}"}],
                max_tokens=2000, timeout=60, feature=feature,
            )
        except ds.DailyCapReached:
            return [], "cap"
        except Exception as e:
            logger.warning(f"AI pick failed, keeping standard recommendations: {e}")
            return [], "error"
        valid, picks = set(ids), []
        for p in out.get("picks") or []:  # trust boundary: known ids, no repeats, short text
            try:
                bid, reason = int(p["id"]), str(p.get("reason") or "").strip()[:80]
            except (TypeError, ValueError, KeyError):
                continue
            if bid in valid and bid not in {i for i, _ in picks}:
                picks.append((bid, reason))
            if len(picks) >= top_k:
                break
        if picks:
            conn.execute("INSERT OR REPLACE INTO ai_rerank_cache (key, scores) VALUES (?, ?)", (key, json.dumps({"picks": picks}, ensure_ascii=False)))
            conn.commit()
        return picks, "" if picks else "error"
    finally:
        conn.close()
