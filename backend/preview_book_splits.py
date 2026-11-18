"""
Read-only preview of splitting mismerged records (step 4 of 同书归并与误合并拆分方案.md).

For every record holding several files, lists which files stay, which would become new books (grouped as they
would be created) and which wait for the user. Records with favourites, ratings, notes or reading progress come
first: those are the ones where a wrong split costs the user something. Writes nothing to the library.

The report names books, so it goes under data/ (not in git).

Usage:
  .venv/bin/python backend/preview_book_splits.py            # writes data/split_preview.md
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import DATA_DIR  # noqa: E402
from database import get_conn  # noqa: E402
from mismerge_service import load_decisions, preview_book  # noqa: E402
from same_work import DIFFERENT, SAME, UNCERTAIN  # noqa: E402

REPORT_PATH = DATA_DIR / "split_preview.md"
_USER_DATA_SQL = (
    "is_favorite = 1 OR COALESCE(user_rating, 0) > 0 OR COALESCE(user_notes, '') != '' "
    "OR COALESCE(read_progress, 0) > 0 OR COALESCE(reading_status, 'unread') != 'unread'"
)


def _file_name(row: Mapping[str, Any]) -> str:
    return os.path.basename(row["original_path"] or row["archived_path"] or "")


def render_book(book: Mapping[str, Any], rows: List[Mapping[str, Any]], preview: Dict[str, Any]) -> List[str]:
    names = {r["id"]: _file_name(r) for r in rows}
    lines = [f"### #{book['id']} {book['title']}", "", f"- 保留（锚点）：{names[preview['anchor']]}"]
    lines += [f"- 保留：{names[i]}" for i in preview[SAME]]
    for n, group in enumerate(preview["new_books"], 1):
        lines += [f"- 拆出 → 新书 {n}：{names[i]}" for i in group]
    lines += [f"- 待确认：{names[i]}" for i in preview[UNCERTAIN]]
    return lines + [""]


def build_report(conn) -> str:
    books = conn.execute(
        f"SELECT id, title, ({_USER_DATA_SQL}) AS has_user_data FROM books WHERE COALESCE(is_deleted, 0) = 0 "
        "AND id IN (SELECT book_id FROM book_files GROUP BY book_id HAVING COUNT(*) > 1) ORDER BY id"
    ).fetchall()
    sections: Dict[str, List[str]] = {"user": [], DIFFERENT: [], UNCERTAIN: []}
    counts = {"books": len(books), DIFFERENT: 0, UNCERTAIN: 0, "new_books": 0}
    decisions = load_decisions()
    for book in books:
        rows = conn.execute("SELECT * FROM book_files WHERE book_id = ? ORDER BY id", (book["id"],)).fetchall()
        preview = preview_book(rows, book["title"], decisions)
        if not preview[DIFFERENT] and not preview[UNCERTAIN]:
            continue
        kind = "user" if book["has_user_data"] else (DIFFERENT if preview[DIFFERENT] else UNCERTAIN)
        sections[kind] += render_book(book, rows, preview)
        counts[DIFFERENT] += len(preview[DIFFERENT])
        counts[UNCERTAIN] += len(preview[UNCERTAIN])
        counts["new_books"] += len(preview["new_books"])
    return _assemble(counts, sections)


def _assemble(counts: Dict[str, int], sections: Dict[str, List[str]]) -> str:
    head = [
        "# 误合并拆分预览（只读）", "",
        f"检查了 {counts['books']} 个多文件书目：{counts[DIFFERENT]} 个文件将拆出，组成 {counts['new_books']} 本新书；"
        f"{counts[UNCERTAIN]} 个文件待确认。用户数据（收藏、评分、笔记、阅读状态与进度）都留在原书目上。", "",
    ]
    titles = {"user": "## 有用户数据的书目（请先看这里）", DIFFERENT: "## 将拆分的书目", UNCERTAIN: "## 只有待确认文件的书目"}
    for kind, title in titles.items():
        head += [title, ""] + (sections[kind] or ["（无）", ""])
    return "\n".join(head)


def main() -> None:
    conn = get_conn()
    try:
        report = build_report(conn)
    finally:
        conn.close()
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(report.split("\n")[2])
    print(f"written to {REPORT_PATH}")


if __name__ == "__main__":
    main()
