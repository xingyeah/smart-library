"""
Offline book metadata enrichment with DeepSeek (deepseek-flash, thinking off) into table book_ai_meta.

Per book: clean title (marketing blurbs removed), zh/en keywords, level, kind, AI category, subfield.
Resumable: books whose input (title/summary/TOC) and prompt version are unchanged are skipped.

Usage:
  .venv/bin/python backend/enrich_ai.py run [--limit N] [--ids 1 2 3] [--category 金融与经济学] [--force] [--workers 8]
  .venv/bin/python backend/enrich_ai.py report          # category mismatches + tag distribution
"""

import argparse
import hashlib
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import deepseek_client as ds  # noqa: E402
from database import get_conn, init_db  # noqa: E402

VERSION = 1  # bump when the prompt or tag vocabulary changes; books get re-enriched
BATCH = 5  # books per call: shares the system prompt across books
LEVELS = ("入门", "普及", "进阶", "专业")
KINDS = ("技术工程", "学术理论", "实用指南", "投资理财", "纪实调查", "传记回忆", "历史叙事", "小说文学", "随笔评论", "套装合集")
CATEGORIES = (
    "历史与地缘政治", "文学小说与艺术", "商业管理与创投", "个人成长与生活", "社科政治与法律", "自然科学与健康", "金融与经济学",
    "哲学宗教与通识", "心理学与脑科学", "数学与统计", "人工智能与技术", "人物传记与回忆录", "销售与市场营销", "产品与UX设计",
)
SYSTEM = (
    "你为个人图书馆整理书目信息。对每本书，依据书名、作者、出版社、现有分类、简介和目录判断，输出：\n"
    "- clean_title：只删除宣传语、推荐语、促销信息、丛书名和格式后缀，其余一字不改：保留原有语言和简繁体（繁体书名仍用繁体，不翻译不转换），"
    "中英对照书名保留中文书名及括号里的英文原名（英文原名过长可截到主标题），原书名已干净就原样返回\n"
    "- keywords：5-10 个检索关键词，中文为主，重要概念附英文（如 区块链、blockchain、共识机制）\n"
    f"- level：难度，只能是 {'/'.join(LEVELS)} 之一（入门=零基础大众读物，普及=通俗但有深度，进阶=需要基础，专业=教材专著）\n"
    f"- kind：类型，只能是 {'/'.join(KINDS)} 之一（技术工程=讲技术原理与实现；投资理财=以投资收益为导向；纪实调查=新闻纪实与事件调查）\n"
    f"- category：领域，只能是以下之一：{'、'.join(CATEGORIES)}\n"
    "- subfield：细分方向，2-8 个字（如 区块链、明史、行为经济学）\n"
    '只输出 json，格式：{"books": [{"id": 123, "clean_title": "…", "keywords": ["…"], "level": "普及", "kind": "技术工程", '
    '"category": "人工智能与技术", "subfield": "区块链"}]}'
)


def book_input(r: Any) -> Dict[str, Any]:
    toc = " | ".join((r["toc_text"] or "").split(" | ")[:30])[:500]
    return {
        "id": int(r["id"]),
        "title": (r["title"] or "")[:150],
        "subtitle": (r["subtitle"] or "")[:80],
        "author": (r["author"] or "")[:60],
        "publisher": (r["publisher"] or "")[:40],
        "category": r["category"] or "",
        "summary": (r["summary"] or "")[:300],
        "toc": toc,
    }


def input_hash(b: Dict[str, Any]) -> str:
    return hashlib.sha1(f"v{VERSION}|{json.dumps(b, ensure_ascii=False, sort_keys=True)}".encode("utf-8")).hexdigest()[:16]


# Marketing segments: full-width 【…】 or a long （…） with Chinese text; "(English original)" is not marketing
_DIRTY_TITLE = re.compile(r"【[^】]{6,}】|（[^）]*[\u4e00-\u9fff][^）]{8,}）")


def is_deletion_of(clean: str, original: str) -> bool:
    """clean keeps original characters in order and adds nothing (no rewording, no 简繁 conversion)."""
    it = iter(original)
    return all(ch in it for ch in clean)


def clean_title_for(original: str, proposed: str) -> str:
    proposed = (proposed or "").strip()
    if proposed and _DIRTY_TITLE.search(original) and is_deletion_of(proposed, original):
        return proposed
    return original.strip()


def validate(item: Dict[str, Any], titles: Dict[int, str]) -> Optional[Dict[str, Any]]:
    """Trust boundary: only known ids and in-vocabulary tags are stored; titles may only lose marketing text."""
    try:
        bid = int(item.get("id"))
    except (TypeError, ValueError):
        return None
    if bid not in titles:
        return None
    kws = [str(k).strip()[:30] for k in (item.get("keywords") or []) if str(k).strip()][:12]
    return {
        "book_id": bid,
        "clean_title": clean_title_for(titles[bid], str(item.get("clean_title") or ""))[:150],
        "keywords": json.dumps(kws, ensure_ascii=False),
        "level": item.get("level") if item.get("level") in LEVELS else "",
        "kind": item.get("kind") if item.get("kind") in KINDS else "",
        "ai_category": item.get("category") if item.get("category") in CATEGORIES else "",
        "subfield": str(item.get("subfield") or "").strip()[:20],
    }


def enrich_batch(books: List[Dict[str, Any]], retries: int = 4) -> List[Dict[str, Any]]:
    user = json.dumps({"books": books}, ensure_ascii=False)
    for attempt in range(retries):
        try:
            out, usage = ds.chat_json(
                [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                max_tokens=4000, timeout=120, feature="enrich",
            )
            titles = {b["id"]: b["title"] for b in books}
            rows = [v for v in (validate(i, titles) for i in out.get("books") or []) if v]
            for r in rows:
                r["_usage"] = usage
            return rows
        except Exception as e:  # rate limit / timeout / bad JSON: back off and retry, never pause the whole run
            wait = 2 ** attempt * 3
            print(f"  batch {books[0]['id']}..: {e}; retry in {wait}s", flush=True)
            time.sleep(wait)
    return []


def cmd_run(limit: int, ids: List[int], category: str, force: bool, workers: int) -> None:
    init_db()
    conn = get_conn()
    try:
        where, params = ["1=1"], []
        if ids:
            where.append(f"b.id IN ({','.join('?' * len(ids))})")
            params += ids
        if category:
            where.append("b.category = ?")
            params.append(category)
        rows = conn.execute(
            f"SELECT b.*, m.input_hash AS done_hash FROM books b LEFT JOIN book_ai_meta m ON m.book_id = b.id "
            f"WHERE {' AND '.join(where)} ORDER BY b.id",
            params,
        ).fetchall()
    finally:
        conn.close()
    todo = []
    for r in rows:
        b = book_input(r)
        h = input_hash(b)
        if force or r["done_hash"] != h:
            todo.append((b, h))
    if limit:
        todo = todo[:limit]
    print(f"{len(todo)} books to enrich ({len(rows) - len(todo)} up to date)", flush=True)
    batches = [todo[i : i + BATCH] for i in range(0, len(todo), BATCH)]
    tokens_in = tokens_out = stored = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(enrich_batch, [b for b, _ in batch]): batch for batch in batches}
        for n, fut in enumerate(as_completed(futures), 1):
            batch = futures[fut]
            hashes = {b["id"]: h for b, h in batch}
            results = fut.result()
            if results:
                usage = results[0].pop("_usage", {})
                tokens_in += usage.get("prompt_tokens", 0)
                tokens_out += usage.get("completion_tokens", 0)
            conn = get_conn()
            try:
                for r in results:
                    r.pop("_usage", None)
                    conn.execute(
                        "INSERT OR REPLACE INTO book_ai_meta (book_id, clean_title, keywords, level, kind, ai_category, "
                        "subfield, input_hash, model, version, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
                        (r["book_id"], r["clean_title"], r["keywords"], r["level"], r["kind"], r["ai_category"],
                         r["subfield"], hashes[r["book_id"]], ds.FLASH_MODEL, VERSION),
                    )
                conn.commit()
            finally:
                conn.close()
            stored += len(results)
            if n % 20 == 0 or n == len(batches):
                # Peak price for deepseek-flash: ¥2/M input (cache miss), ¥8/M output; off-peak is half
                cost = tokens_in / 1e6 * 2 + tokens_out / 1e6 * 8
                print(f"[{n}/{len(batches)}] stored {stored} | tokens in {tokens_in} out {tokens_out} | ~¥{cost:.2f} (peak) | {time.time() - t0:.0f}s", flush=True)


def cmd_report() -> None:
    conn = get_conn()
    try:
        n = conn.execute("SELECT COUNT(*) FROM book_ai_meta").fetchone()[0]
        print(f"enriched: {n}")
        for col in ("level", "kind", "ai_category"):
            print(col, [tuple(r) for r in conn.execute(f"SELECT {col}, COUNT(*) FROM book_ai_meta GROUP BY 1 ORDER BY 2 DESC")])
        mism = conn.execute(
            "SELECT b.id, b.title, b.category, m.ai_category FROM books b JOIN book_ai_meta m ON m.book_id = b.id "
            "WHERE m.ai_category != '' AND m.ai_category != b.category"
        ).fetchall()
        print(f"category mismatches: {len(mism)}")
        out = Path(__file__).resolve().parent.parent / "data" / "ai_category_mismatches.tsv"
        out.write_text("id\ttitle\tcategory\tai_category\n" + "".join(
            f"{r['id']}\t{r['title']}\t{r['category']}\t{r['ai_category']}\n" for r in mism), encoding="utf-8")
        print(f"wrote {out}")
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--ids", type=int, nargs="*", default=[])
    r.add_argument("--category", default="")
    r.add_argument("--force", action="store_true")
    r.add_argument("--workers", type=int, default=8)
    sub.add_parser("report")
    args = ap.parse_args()
    if args.cmd == "run":
        cmd_run(args.limit, args.ids, args.category, args.force, args.workers)
    else:
        cmd_report()


if __name__ == "__main__":
    main()
