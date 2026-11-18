"""
Repair machine-made bilingual titles ('认知升级与高效生活指南 (Tom Vanderbilt)').

A batch job gave thousands of English books a Chinese name; many names are shared by unrelated books or are
word-for-word nonsense, and some lost the real English title. This tool rebuilds them in three reviewable steps;
nothing in `books` changes before `apply`.

  plan       recover the real English title of every '中文 (English)' book into the title_repair staging table
  translate  DeepSeek translates the staged titles (validated; a rejected one keeps the English title only)
  sample     print before -> after for a random sample
  apply      back up the DB, write titles and derived search fields, re-encode the vectors

Usage:
  .venv/bin/python backend/repair_titles.py plan
  .venv/bin/python backend/repair_titles.py translate --limit 200
  .venv/bin/python backend/repair_titles.py sample --n 30
  .venv/bin/python backend/repair_titles.py apply
"""

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import deepseek_client as ds  # noqa: E402
import enrich_ai  # noqa: E402
from ebook_parser.filenames import english_title_from_path  # noqa: E402
from database import backup_database, get_conn, init_db  # noqa: E402
from parser import build_cjk_search_tokens, normalize_for_dedup, parse_filename_metadata  # noqa: E402

BILINGUAL_TITLE_GLOB = "*[一-龥]* (*[A-Za-z][A-Za-z]*)"
TRANSLATE_BATCH_SIZE = 20
MAX_ZH_TITLE_CHARS = 40
SEARCH_TOKEN_SUMMARY_CHARS = 300
_CJK = re.compile(r"[一-鿿]")
TRANSLATE_SYSTEM = (
    "你是中外图书编辑。把每本英文书的书名译成中文书名：有国内公认译名的用公认译名；没有的按书名原意直译，"
    "要具体到这本书，不要用“某某理论与方法”“某某指南”这类可以套在任何书上的泛称；人名、专有名词音译或保留。"
    "只译主书名，不超过 20 个字，不加书名号。不同的书必须给出不同的译名。"
    '只输出 json，格式：{"titles": {"<书的id>": "中文书名"}}'
)


def english_part_of(title: str) -> str:
    """'中文 (English)' -> 'English'."""
    if " (" not in title:
        return ""
    english = title.split(" (", 1)[1].strip()
    return english[:-1].strip() if english.endswith(")") else english


def work_key(en_title: str) -> str:
    """Same English title (case, spacing and punctuation aside) means the same work, e.g. two copies or editions."""
    return re.sub(r"[^a-z0-9]", "", en_title.lower())


def accept_translations(reply: Dict[str, Any], en_titles: Dict[int, str], taken: Dict[str, str]) -> Dict[int, str]:
    """Translations that pass the checks: id from this batch, Chinese, short, and not already given to another work.

    `en_titles` is the batch as {book_id: English title}. `taken` maps every accepted Chinese title to the work it
    names and is updated in place; a different work proposing the same name is rejected (the earlier fake titles
    were exactly such shared names), while another copy of the same work may reuse it.
    """
    accepted: Dict[int, str] = {}
    valid = set(en_titles)
    for raw_id, raw_title in (reply.get("titles") or {}).items():
        try:
            book_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        zh = str(raw_title or "").strip().strip("《》")
        if book_id not in valid or not _CJK.search(zh) or len(zh) > MAX_ZH_TITLE_CHARS:
            continue
        work = work_key(en_titles[book_id])
        if taken.get(zh, work) != work:
            continue
        taken[zh] = work
        accepted[book_id] = zh
    return accepted


def cmd_plan() -> None:
    init_db()
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT b.id, b.title, (SELECT original_path FROM book_files f WHERE f.book_id = b.id "
            "ORDER BY f.is_primary DESC LIMIT 1) AS path FROM books b WHERE b.title GLOB ?",
            (BILINGUAL_TITLE_GLOB,),
        ).fetchall()
        from_file = 0
        for r in rows:
            en_title = english_title_from_path(r["path"] or "")
            source = "filename"
            if not en_title:
                en_title, source = english_part_of(r["title"]), "current_title"
            from_file += source == "filename"
            conn.execute(
                "INSERT INTO title_repair (book_id, old_title, en_title, en_source) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(book_id) DO UPDATE SET en_title = excluded.en_title, en_source = excluded.en_source "
                "WHERE status = 'planned'",
                (r["id"], r["title"], en_title, source),
            )
        conn.commit()
        print(f"planned {len(rows)} books | English title from file name: {from_file}, from current title: {len(rows) - from_file}")
    finally:
        conn.close()


def _translate_batch(batch: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    lines = "\n".join(f"[{b['id']}] {b['en_title']} | {b['author']} | {b['category']}" for b in batch)
    return ds.chat_json(
        [{"role": "system", "content": TRANSLATE_SYSTEM}, {"role": "user", "content": f"待译图书（id | 书名 | 作者 | 分类）：\n{lines}"}],
        max_tokens=2000, timeout=90, feature="enrich",
    )


def cmd_translate(limit: int, workers: int) -> None:
    conn = get_conn()
    try:
        _staging(conn)
        todo = [dict(r) for r in conn.execute(
            "SELECT t.book_id AS id, t.en_title, COALESCE(b.author, '') AS author, COALESCE(b.category, '') AS category "
            "FROM title_repair t JOIN books b ON b.id = t.book_id WHERE t.status = 'planned' AND t.en_title != '' ORDER BY t.book_id"
        )]
        taken = {r["zh_title"]: work_key(r["en_title"]) for r in conn.execute("SELECT zh_title, en_title FROM title_repair WHERE zh_title != ''")}
    finally:
        conn.close()
    if limit:
        todo = todo[:limit]
    batches = [todo[i : i + TRANSLATE_BATCH_SIZE] for i in range(0, len(todo), TRANSLATE_BATCH_SIZE)]
    tokens_in = tokens_out = translated = rejected = 0
    started = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_translate_batch, batch): batch for batch in batches}
        for done, fut in enumerate(as_completed(futures), 1):
            batch = futures[fut]
            try:
                reply, usage = fut.result()
            except Exception as e:  # rate limit / timeout / bad JSON: the batch stays 'planned' for the next run
                print(f"  batch {batch[0]['id']}.. failed, left for a retry: {e}", flush=True)
                continue
            tokens_in += usage.get("prompt_tokens", 0)
            tokens_out += usage.get("completion_tokens", 0)
            accepted = accept_translations(reply, {b["id"]: b["en_title"] for b in batch}, taken)  # main thread only: no lock
            conn = get_conn()
            try:
                for b in batch:
                    zh = accepted.get(b["id"], "")
                    conn.execute(
                        "UPDATE title_repair SET zh_title = ?, status = ? WHERE book_id = ?",
                        (zh, "translated" if zh else "english_only", b["id"]),
                    )
                conn.commit()
            finally:
                conn.close()
            translated += len(accepted)
            rejected += len(batch) - len(accepted)
            if done % 20 == 0 or done == len(batches):
                cost = tokens_in / 1e6 * 2 + tokens_out / 1e6 * 8  # deepseek-flash peak price, CNY per 1M tokens
                print(f"[{done}/{len(batches)}] translated {translated} | english only {rejected} | ~¥{cost:.2f} (peak) | {time.time() - started:.0f}s", flush=True)


def new_title(en_title: str, zh_title: str) -> str:
    return f"{zh_title} ({en_title})" if zh_title else en_title


def cmd_sample(n: int) -> None:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT old_title, en_title, zh_title, status FROM title_repair WHERE status != 'planned' ORDER BY RANDOM() LIMIT ?", (n,)
        ).fetchall()
        counts = conn.execute("SELECT status, COUNT(*) FROM title_repair GROUP BY status").fetchall()
    finally:
        conn.close()
    print({r[0]: r[1] for r in counts})
    for r in rows:
        print(f"- {r['old_title']}\n  -> {new_title(r['en_title'], r['zh_title'])}")


def cmd_apply() -> None:
    from archiver import rebuild_vector_cache

    print(f"backup: {backup_database()}")
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT t.book_id, t.en_title, t.zh_title, b.* FROM title_repair t JOIN books b ON b.id = t.book_id "
            "WHERE t.status IN ('translated', 'english_only') AND t.en_title != ''"
        ).fetchall()
        for r in rows:
            title = new_title(r["en_title"], r["zh_title"])
            norm_title, norm_author = normalize_for_dedup(title, r["author"] or "")
            tokens = build_cjk_search_tokens(
                f"{title} {r['author'] or ''} {r['category'] or ''} {(r['summary'] or '')[:SEARCH_TOKEN_SUMMARY_CHARS]}", include_pinyin=True
            )
            conn.execute(
                "UPDATE books SET title = ?, norm_title = ?, norm_author = ?, pinyin_search = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (title, norm_title, norm_author, tokens, r["book_id"]),
            )
            # Cards show clean_title; keep the tags (they describe the content) and mark the row current for the new title
            fresh = conn.execute("SELECT * FROM books WHERE id = ?", (r["book_id"],)).fetchone()
            conn.execute(
                "UPDATE book_ai_meta SET clean_title = ?, input_hash = ? WHERE book_id = ?",
                (title, enrich_ai.input_hash(enrich_ai.book_input(fresh)), r["book_id"]),
            )
            conn.execute("UPDATE title_repair SET status = 'applied' WHERE book_id = ?", (r["book_id"],))
        conn.commit()
    finally:
        conn.close()
    print(f"applied {len(rows)} titles; re-encoding vectors...")
    print(rebuild_vector_cache(reencode_ids=[int(r["book_id"]) for r in rows]))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    tr = sub.add_parser("translate")
    tr.add_argument("--limit", type=int, default=0)
    tr.add_argument("--workers", type=int, default=8)
    sm = sub.add_parser("sample")
    sm.add_argument("--n", type=int, default=30)
    sub.add_parser("apply")
    args = ap.parse_args()
    if args.cmd == "plan":
        cmd_plan()
    elif args.cmd == "translate":
        cmd_translate(args.limit, args.workers)
    elif args.cmd == "sample":
        cmd_sample(args.n)
    else:
        cmd_apply()


if __name__ == "__main__":
    main()
