"""
Grow the search eval set with DeepSeek (tests/search_eval/generated_queries.json).

  generate  v4-pro writes ~10 natural-language queries per category, grounded in real titles from the library
  label     pools the top 20 of shelf search and describe-to-recommend per query; v4-pro grades unjudged books 0/1/2
            (re-run after ranking changes: only new books get labeled)
  review    writes tests/search_eval/review/review.md (book titles; gitignored) for a 20-query human spot check
  verify    marks reviewed query ids as human-verified so they join the quality gate

Usage:
  .venv/bin/python backend/eval_build.py generate --per-category 10
  .venv/bin/python backend/eval_build.py label --workers 6
  .venv/bin/python backend/eval_build.py review --n 20
  .venv/bin/python backend/eval_build.py verify gen-03-02 gen-07-05 ...
"""

import argparse
import json
import random
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))

import deepseek_client as ds  # noqa: E402
import search_engine as se  # noqa: E402
from database import get_conn  # noqa: E402

GEN_PATH = Path(__file__).resolve().parent.parent / "tests" / "search_eval" / "generated_queries.json"
REVIEW_DIR = GEN_PATH.parent / "review"
QUERY_TYPES = {
    "known_item": "按书名找书（可以只记得部分书名）",
    "author": "按作者找书",
    "topic": "用一句自然语言描述想读的主题",
    "style": "带阅读风格要求，如入门/零基础/通俗、硬核/教材、讲技术原理、经典必读",
    "negation": "带排除条件，如“不要鸡汤”“不看小说”",
    "english": "用英文提问",
    "traditional": "用繁体中文提问",
}
GEN_SYSTEM = (
    "你在为个人图书馆的搜索系统编写测试查询。根据给出的分类和该分类下的真实藏书样本，写出读者真实会输入的查询。\n"
    "要求：查询要多样、自然、具体；known_item 和 author 必须对应样本里真实存在的书或作者；其余类型围绕该分类的主题，"
    "不要照抄书名。每条附一句 intent，说明读者真正想要什么样的书（包括难度、类型等隐含要求），用于后续人工判定相关性。\n"
    '只输出 json，格式：{"queries": [{"type": "topic", "query": "…", "intent": "…"}]}'
)
LABEL_SYSTEM = (
    "你是图书检索相关性评审。给出读者查询、读者意图和候选书列表，为每本书打相关度：\n"
    "2 = 正是读者想要的（主题对，且满足难度/类型/作者/排除等全部要求）；\n"
    "1 = 主题相关但不完全满足要求，或只部分相关；\n"
    "0 = 不相关，或明显违背读者要求（如要技术原理却是投资指南，要入门却是专业教材）。\n"
    '只依据给出的书名、作者、分类和简介判断。只输出 json，格式：{"grades": {"<书的id>": 2, "<书的id>": 0}}'
)
_lock = threading.Lock()


def load() -> Dict[str, Any]:
    if GEN_PATH.exists():
        return json.loads(GEN_PATH.read_text(encoding="utf-8"))
    return {
        "_doc": "DeepSeek-written queries; judged = {book_id: grade 0/1/2} pooled from shelf + describe top 20. "
        "verified = a human checked the labels; only verified queries count in the quality gate.",
        "queries": [],
    }


def save(data: Dict[str, Any]) -> None:
    tmp = GEN_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(GEN_PATH)


def cmd_generate(per_category: int, seed: int) -> None:
    data = load()
    done_cats = {q["category"] for q in data["queries"]}
    conn = get_conn()
    try:
        cats = [r[0] for r in conn.execute("SELECT category FROM books GROUP BY category HAVING COUNT(*) >= 30 ORDER BY COUNT(*) DESC")]
        rng = random.Random(seed)
        for ci, cat in enumerate(cats):
            if cat in done_cats:
                continue
            rows = conn.execute("SELECT title, author FROM books WHERE category = ?", (cat,)).fetchall()
            sample = rng.sample(rows, min(30, len(rows)))
            books = "\n".join(f"- 《{r['title'][:50]}》 {r['author'][:30]}" for r in sample)
            types = "\n".join(f"- {k}: {v}" for k, v in QUERY_TYPES.items())
            user = (
                f"分类：{cat}\n藏书样本：\n{books}\n\n查询类型：\n{types}\n\n"
                f"请写 {per_category} 条查询：known_item、author、style、negation、english、traditional 各 1 条，其余为 topic。"
            )
            out, usage = ds.chat_json(
                [{"role": "system", "content": GEN_SYSTEM}, {"role": "user", "content": user}],
                model=ds.PRO_MODEL, max_tokens=4000, timeout=180, feature="eval",
            )
            new = [q for q in out.get("queries", []) if q.get("query") and q.get("type") in QUERY_TYPES]
            for i, q in enumerate(new):
                data["queries"].append({
                    "id": f"gen-{ci:02d}-{i:02d}", "category": cat, "type": q["type"],
                    "query": q["query"].strip(), "intent": (q.get("intent") or "").strip(),
                    "verified": False, "judged": {},
                })
            save(data)
            print(f"{cat}: {len(new)} queries (tokens in {usage.get('prompt_tokens')}, out {usage.get('completion_tokens')})", flush=True)
    finally:
        conn.close()


def pool_candidates(query: str) -> List[Dict[str, Any]]:
    seen: Dict[int, Dict[str, Any]] = {}
    for b in se.instant_search(q=query, page_size=20)["items"] + se.recommend_by_description(query, top_k=20)["items"]:
        seen.setdefault(int(b["id"]), b)
    return list(seen.values())


def label_one(q: Dict[str, Any]) -> int:
    with _lock:  # search engine shares SQLite connections and model sessions; keep pooling serial
        cands = [b for b in pool_candidates(q["query"]) if str(b["id"]) not in q["judged"]]
    if not cands:
        return 0
    lines = "\n".join(
        f"[{b['id']}] 《{(b.get('title') or '')[:60]}》 | {(b.get('author') or '')[:30]} | {b.get('category') or ''} | "
        f"{(b.get('summary') or '')[:150]}"
        for b in cands
    )
    user = f"读者查询：{q['query']}\n读者意图：{q['intent']}\n\n候选书：\n{lines}"
    out, _ = ds.chat_json(
        [{"role": "system", "content": LABEL_SYSTEM}, {"role": "user", "content": user}],
        model=ds.PRO_MODEL, thinking=True, max_tokens=16000, timeout=300, feature="eval",
    )
    valid = {str(b["id"]) for b in cands}
    grades = {k: int(v) for k, v in (out.get("grades") or {}).items() if k in valid and v in (0, 1, 2)}
    with _lock:
        q["judged"].update(grades)
    return len(grades)


def cmd_label(workers: int, only: str) -> None:
    data = load()
    se.warmup_search_engine()
    todo = [q for q in data["queries"] if not only or q["id"] == only]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(label_one, q): q for q in todo}
        for n, fut in enumerate(as_completed(futures), 1):
            q = futures[fut]
            try:
                print(f"[{n}/{len(todo)}] {q['id']} +{fut.result()} labels", flush=True)
            except Exception as e:
                print(f"[{n}/{len(todo)}] {q['id']} FAILED: {e}", flush=True)
            if n % 10 == 0:
                with _lock:
                    save(data)
    save(data)


def cmd_review(n: int, seed: int) -> None:
    data = load()
    conn = get_conn()
    rng = random.Random(seed)
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for q in data["queries"]:
        if q["judged"] and not q["verified"]:
            by_type.setdefault(q["type"], []).append(q)
    picked: List[Dict[str, Any]] = []
    while len(picked) < n and any(by_type.values()):  # round-robin across query types
        for t in list(by_type):
            if by_type[t] and len(picked) < n:
                picked.append(by_type[t].pop(rng.randrange(len(by_type[t]))))
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    lines = ["# 评测集抽查", "", "每条查询下列出 DeepSeek 的判定（2 正中 / 1 相关 / 0 不相关）。标错的请告诉我：查询 id + 书 id + 正确等级。", ""]
    for q in picked:
        lines += [f"## {q['id']} · {q['type']} · {q['category']}", f"**查询**：{q['query']}", f"**意图**：{q['intent']}", ""]
        ids = sorted(q["judged"], key=lambda k: -q["judged"][k])
        rows = {str(r["id"]): r for r in conn.execute(
            f"SELECT id, title, author FROM books WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else {}
        for k in ids:
            r = rows.get(k)
            if r:
                lines.append(f"- **{q['judged'][k]}** · [{k}] 《{r['title'][:50]}》 {r['author'][:20]}")
        lines.append("")
    conn.close()
    out = REVIEW_DIR / "review.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out} ({len(picked)} queries)")


def cmd_verify(ids: List[str]) -> None:
    data = load()
    hit = 0
    for q in data["queries"]:
        if q["id"] in ids:
            q["verified"] = True
            hit += 1
    save(data)
    print(f"verified {hit}/{len(ids)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--per-category", type=int, default=10)
    g.add_argument("--seed", type=int, default=7)
    lb = sub.add_parser("label")
    lb.add_argument("--workers", type=int, default=6)
    lb.add_argument("--only", default="")
    rv = sub.add_parser("review")
    rv.add_argument("--n", type=int, default=20)
    rv.add_argument("--seed", type=int, default=7)
    vf = sub.add_parser("verify")
    vf.add_argument("ids", nargs="+")
    args = ap.parse_args()
    if args.cmd == "generate":
        cmd_generate(args.per_category, args.seed)
    elif args.cmd == "label":
        cmd_label(args.workers, args.only)
    elif args.cmd == "review":
        cmd_review(args.n, args.seed)
    else:
        cmd_verify(args.ids)


if __name__ == "__main__":
    main()
