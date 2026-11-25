"""DeepSeek's two jobs for a tonight search: read the sentence, then judge the books found for it.

Both answers are kept in SQLite: a sentence is read once, and a book judged for a sentence keeps its verdict, so
asking again or moving to the next batch costs nothing until new books need judging.
"""

import json
import logging
from typing import Any, Dict, List, Sequence

import deepseek_client as ds
from database import get_conn, write_tx

logger = logging.getLogger("tonight_ai")

AI_FEATURE = "search"
MAX_SEARCH_LINES = 4
SUMMARY_CHARS = 160
UNDERSTAND_TIMEOUT_S = 20
JUDGE_TIMEOUT_S = 45
JUDGE_MAX_TOKENS = 4000
MAX_REASON_CHARS = 60
FEEDBACK_TITLES = 8

UNDERSTAND_SYSTEM = (
    "你是私人图书馆的检索助手。读者用一句话说想读什么，你把它改写成检索用的结构。"
    "search_lines 是 2-4 条短检索句（每条 6-20 字），覆盖这句话的不同角度，至少一条英文；"
    "keywords 是 3-8 个具体概念词（不要“对于、带来、两者”这类虚词）；"
    "categories 从给出的书架分类里选 0-3 个最相关的；"
    "avoid 用一句话说读者明显不想要的书（比如要纪实却可能搜出小说），没有就留空。"
    '只输出 json，例如：{"search_lines": ["人工智能 企业管理变革", "AI and management"], '
    '"keywords": ["人工智能", "管理"], "categories": ["商业管理与创投"], "avoid": "纯技术编程书"}'
)

JUDGE_SYSTEM = (
    "你是私人图书馆的选书顾问。给出读者想读什么、你对这句话的理解、读者在这句话下想读过和跳过的书，以及候选书。"
    "为每本候选书打 0-10 分：10=正是读者想要的；6=值得推荐；3=沾边但不是读者要的；0=无关或违背要求。"
    "只依据给出的信息判断，不要因为书有名就给高分。"
    "score 不低于 6 的书写 reason：一句中文（不超过 30 字）说为什么值得读，写给读者看，不要复述书名；低于 6 分的 reason 留空。"
    '只输出 json，例如：{"books": {"123": {"score": 8, "reason": "讲 AI 如何重塑管理决策，正对题"}}}'
)


class AiUnavailable(RuntimeError):
    """DeepSeek cannot be used now; `reason` is "nokey", "cap" or "error"."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _call_json(system: str, user: str, **options: Any) -> Dict[str, Any]:
    if not ds.api_key():
        raise AiUnavailable("nokey")
    try:
        reply, _ = ds.chat_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user}], feature=AI_FEATURE, **options
        )
    except ds.DailyCapReached as error:
        raise AiUnavailable("cap") from error
    except Exception as error:
        logger.warning(f"tonight AI call failed: {error}")
        raise AiUnavailable("error") from error
    return reply


def _clean_understanding(reply: Dict[str, Any], query: str, categories: Sequence[str]) -> Dict[str, Any]:
    """Trust boundary: keeps only short strings, known categories, and always searches the sentence itself."""
    def strings(key: str, limit: int) -> List[str]:
        values = reply.get(key) if isinstance(reply.get(key), list) else []
        return [str(v).strip()[:60] for v in values if isinstance(v, str) and str(v).strip()][:limit]

    lines = strings("search_lines", MAX_SEARCH_LINES)
    return {
        "search_lines": lines or [query],
        "keywords": strings("keywords", 8),
        "categories": [c for c in strings("categories", 3) if c in categories],
        "avoid": str(reply.get("avoid") or "").strip()[:80],
    }


def understand_query(query: str, categories: Sequence[str]) -> Dict[str, Any]:
    """{search_lines, keywords, categories, avoid} for `query`, read by DeepSeek once and then from the cache."""
    conn = get_conn()
    try:
        row = conn.execute("SELECT payload FROM ai_query_understanding WHERE query = ?", (query,)).fetchone()
    finally:
        conn.close()
    if row:
        return json.loads(row[0])
    reply = _call_json(
        UNDERSTAND_SYSTEM, f"书架分类：{'、'.join(categories)}\n\n读者的话：{query}",
        max_tokens=600, timeout=UNDERSTAND_TIMEOUT_S,
    )
    understanding = _clean_understanding(reply, query, categories)
    with write_tx() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO ai_query_understanding (query, payload) VALUES (?, ?)",
            (query, json.dumps(understanding, ensure_ascii=False)),
        )
    return understanding


def _book_line(book: Dict[str, Any]) -> str:
    title = (book.get("clean_title") or book.get("title") or "")[:60]
    summary = (book.get("summary") or "").replace("\n", " ")[:SUMMARY_CHARS]
    return f"[{book['id']}] 《{title}》 | {(book.get('author') or '')[:30]} | {book.get('category') or ''} | {summary}"


def _judge_prompt(query: str, candidates: List[Dict[str, Any]], context: Dict[str, Any]) -> str:
    liked = "、".join(f"《{t}》" for t in context.get("wanted", [])[:FEEDBACK_TITLES]) or "无"
    disliked = "、".join(f"《{t}》" for t in context.get("skipped", [])[:FEEDBACK_TITLES]) or "无"
    return (
        f"读者想读：{query}\n"
        f"理解：关键词 {'、'.join(context.get('keywords') or [])}；不要：{context.get('avoid') or '无'}\n"
        f"读者在这句话下想读过：{liked}\n读者在这句话下跳过：{disliked}\n\n候选书：\n"
        + "\n".join(_book_line(book) for book in candidates)
    )


def _clean_verdicts(reply: Dict[str, Any], candidate_ids: Sequence[int]) -> Dict[int, Dict[str, Any]]:
    """Trust boundary: only candidates' ids, integer scores 0-10, short reasons."""
    valid = set(candidate_ids)
    verdicts: Dict[int, Dict[str, Any]] = {}
    books = reply.get("books") if isinstance(reply.get("books"), dict) else {}
    for key, value in books.items():
        try:
            book_id, score = int(key), int((value or {}).get("score"))
        except (TypeError, ValueError, AttributeError):
            continue
        if book_id in valid and 0 <= score <= 10:
            verdicts[book_id] = {"score": score, "reason": str(value.get("reason") or "").strip()[:MAX_REASON_CHARS]}
    return verdicts


def judge_books(query: str, candidates: List[Dict[str, Any]], context: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    """{book_id: {score, reason}} for the candidates DeepSeek answered; stored as round max+1 of `query`.
    `context` holds the understanding's keywords / avoid and the titles wanted / skipped under the sentence.
    A candidate it left out is stored with score 0, so it is not sent again."""
    if not candidates:
        return {}
    ids = [int(book["id"]) for book in candidates]
    reply = _call_json(
        JUDGE_SYSTEM, _judge_prompt(query, candidates, context),
        max_tokens=JUDGE_MAX_TOKENS, timeout=JUDGE_TIMEOUT_S,
    )
    verdicts = _clean_verdicts(reply, ids)
    if not verdicts:
        raise AiUnavailable("error")
    with write_tx() as conn:
        last = conn.execute("SELECT COALESCE(MAX(round), 0) FROM ai_query_judgments WHERE query = ?", (query,)).fetchone()
        round_no = int(last[0]) + 1
        conn.executemany(
            "INSERT OR REPLACE INTO ai_query_judgments (query, book_id, score, reason, round) VALUES (?, ?, ?, ?, ?)",
            [(query, i, verdicts.get(i, {}).get("score", 0), verdicts.get(i, {}).get("reason", ""), round_no) for i in ids],
        )
    return verdicts
