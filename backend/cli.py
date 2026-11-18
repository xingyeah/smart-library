import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from archiver import run_organize_pipeline
from database import init_db
from search_engine import instant_search, recommend_by_description, recommend_by_interests


def main() -> None:
    init_db()
    parser = argparse.ArgumentParser(description="我的智能图书馆 CLI")
    sub = parser.add_subparsers(dest="cmd")

    p_org = sub.add_parser("organize", help="扫描并整理电子书目录")
    p_org.add_argument("--limit", type=int, default=None, help="每个源目录扫描上限（默认全量）")
    p_org.add_argument("--ocr", action="store_true", help="对扫描版 PDF 启用 Apple Vision OCR")
    p_org.add_argument("--workers", type=int, default=8, help="并发解析线程数")

    p_search = sub.add_parser("search", help="毫秒级关键词/拼音搜索")
    p_search.add_argument("query", type=str, help="搜索词")

    p_rec = sub.add_parser("recommend", help="按自然语言描述推荐最准 Top 10")
    p_rec.add_argument("description", type=str, help="自然语言找书描述")

    sub.add_parser("interests", help="基于动态兴趣画像生成 Top 10 推荐")

    args = parser.parse_args()
    if args.cmd == "organize":
        t0 = time.time()

        def _cb(st):
            print(
                f"  [进度] 已扫描 {st['scanned_files']} | 新增藏书 {st['indexed_books']} | 合并重复 {st['duplicates_merged']} | 待审冲突 {st['conflicts_found']}",
                flush=True,
            )

        res = run_organize_pipeline(
            limit_per_dir=args.limit,
            run_type="cli",
            enable_ocr=args.ocr,
            max_workers=args.workers,
            progress_callback=_cb,
        )
        res["elapsed_sec"] = round(time.time() - t0, 2)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.cmd == "search":
        res = instant_search(q=args.query, page_size=10)
        print(f"Found {res['total']} books in {res['elapsed_ms']} ms:")
        for b in res["items"]:
            print(f"  - [{b['category']}][{b['primary_format'].upper()}] 《{b['title']}》 {b['author']} (质量分:{b['quality_score']})")
    elif args.cmd == "recommend":
        res = recommend_by_description(args.description, top_k=10)
        print(f"Top 10 for '{args.description}' in {res['elapsed_ms']} ms:")
        for b in res["items"]:
            print(f"  #{b['rank']} ({b['match_score']}%) 《{b['title']}》 - {b['author']} [{b['category']}]")
            print(f"     理由: {b['recommend_reason']}")
            print(f"     简介: {b['summary'][:110]}...")
    elif args.cmd == "interests":
        res = recommend_by_interests(top_k=10)
        print(f"Interest Top 10 in {res['elapsed_ms']} ms:")
        for b in res["items"]:
            print(f"  #{b['rank']} 《{b['title']}》 - {b['author']} | {b['recommend_reason']}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
