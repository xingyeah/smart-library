"""
One-off repair: make each card title (book_ai_meta.clean_title) agree with the book's title again.

Titles were rewritten after clean_title was generated, so ~1/3 of cards showed a different name than the detail.
For each book whose clean_title is no longer its title with only marketing text removed:

  restore  the old clean_title has a Chinese name (made per book from its content; later titles were often
           keyword mash-ups): write it back into books.title, keeping the title's English part unless that
           part is only the author's name
  reset    no Chinese name to restore: clean_title becomes the title (marketing text stripped by rule)

Usage:
  .venv/bin/python backend/sync_display_titles.py preview --n 20
  .venv/bin/python backend/sync_display_titles.py apply
"""

import argparse
import random
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))

import enrich_ai  # noqa: E402
from database import backup_database, get_conn  # noqa: E402
from parser import build_cjk_search_tokens, normalize_for_dedup  # noqa: E402
from repair_titles import SEARCH_TOKEN_SUMMARY_CHARS, english_part_of, new_title  # noqa: E402

_CJK = re.compile(r"[一-鿿]")


def chinese_part_of(title: str) -> str:
    return title.split(" (", 1)[0].strip()


def strip_marketing(title: str) -> str:
    stripped = enrich_ai._DIRTY_TITLE.sub(" ", title)
    stripped = re.sub(r"\s+", " ", stripped)
    return re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", stripped)).strip()


def is_author_only(english: str, author: str) -> bool:
    return bool(english) and english.lower() in (author or "").lower()


def plan_fix(row: Any) -> Dict[str, Any]:
    """{'action': 'restore'|'reset', 'title': new books.title or None, 'clean_title': new card title}."""
    title, clean = row["title"], row["clean_title"]
    old_zh, english = chinese_part_of(clean), english_part_of(title)
    if is_author_only(english, row["author"]):
        english = english_part_of(clean)
    restored = strip_marketing(new_title(english, old_zh) if english else old_zh)
    if _CJK.search(old_zh) and restored != title:
        return {"action": "restore", "title": restored, "clean_title": restored}
    return {"action": "reset", "title": None, "clean_title": strip_marketing(title)}


def drifted_rows(conn: Any) -> List[Any]:
    rows = conn.execute(
        "SELECT b.id, b.title, b.series, b.author, b.category, b.summary, m.clean_title FROM books b "
        "JOIN book_ai_meta m ON m.book_id = b.id WHERE b.is_deleted = 0 AND m.clean_title != ''"
    ).fetchall()
    return [r for r in rows if r["clean_title"] != r["title"] and not enrich_ai.is_deletion_of(r["clean_title"], r["title"])]


def cmd_preview(n: int) -> None:
    conn = get_conn()
    try:
        plans = [(r, plan_fix(r)) for r in drifted_rows(conn)]
    finally:
        conn.close()
    print(Counter(p["action"] for _, p in plans))
    random.seed(1)
    for action in ("restore", "reset"):
        picked = [x for x in plans if x[1]["action"] == action]
        for r, p in random.sample(picked, min(n, len(picked))):
            print(f"[{action}] #{r['id']}\n  card now  : {r['clean_title']}\n  detail now: {r['title']}\n  both after: {p['clean_title']}")


def cmd_apply() -> None:
    from archiver import rebuild_vector_cache

    print(f"backup: {backup_database()}")
    conn = get_conn()
    renamed: List[int] = []
    try:
        for r in drifted_rows(conn):
            plan = plan_fix(r)
            if plan["title"]:
                norm_title, norm_author = normalize_for_dedup(plan["title"], r["author"] or "")
                tokens = build_cjk_search_tokens(
                    f"{plan['title']} {r['author'] or ''} {r['category'] or ''} {(r['summary'] or '')[:SEARCH_TOKEN_SUMMARY_CHARS]}",
                    include_pinyin=True,
                )
                # The books_title_resets_clean_title trigger sets the card title along with it
                conn.execute(
                    "UPDATE books SET title = ?, norm_title = ?, norm_author = ?, pinyin_search = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (plan["title"], norm_title, norm_author, tokens, r["id"]),
                )
                renamed.append(int(r["id"]))
            else:
                conn.execute("UPDATE book_ai_meta SET clean_title = ? WHERE book_id = ?", (plan["clean_title"], r["id"]))
        conn.commit()
    finally:
        conn.close()
    print(f"restored {len(renamed)} titles; re-encoding their vectors...")
    print(rebuild_vector_cache(reencode_ids=renamed))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("preview").add_argument("--n", type=int, default=20)
    sub.add_parser("apply")
    args = ap.parse_args()
    if args.cmd == "preview":
        cmd_preview(args.n)
    else:
        cmd_apply()


if __name__ == "__main__":
    main()
