"""
Backfill book_files.embedded_title / embedded_author / isbn for files imported before those columns existed.

Only files of records holding more than one file are read: those are the ones whose same-work check needs it.
Reads metadata only (EPUB OPF, MOBI/AZW3 EXTH, PDF info dict); no OCR, no covers written. Resumable: a file
is processed once (marked with embedded_title = '-' when it has nothing or is missing).

Usage:
  .venv/bin/python backend/backfill_file_metadata.py --limit 50     # timing sample
  .venv/bin/python backend/backfill_file_metadata.py                # everything left
"""

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Dict

sys.path.insert(0, str(Path(__file__).resolve().parent))

from database import get_conn, init_db  # noqa: E402
from ebook_parser.epub import parse_epub  # noqa: E402
from ebook_parser.filenames import clean_spam_text  # noqa: E402
from ebook_parser.mobi import parse_mobi_azw3  # noqa: E402
from native_bridge import get_native_bridge  # noqa: E402

NOTHING_FOUND = "-"  # distinguishes "read, nothing inside" from "not read yet" ('')
COMMIT_EVERY = 100


def read_embedded_metadata(path: str) -> Dict[str, str]:
    """Title, author and ISBN stored inside the file; empty strings when absent or unreadable."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".epub":
        meta = parse_epub(path, cover_out_path="")
    elif ext in (".mobi", ".azw3", ".azw"):
        meta = parse_mobi_azw3(path, cover_out_path="")
    elif ext == ".pdf":
        res = get_native_bridge().parse_pdf(path, cover_out_path="", enable_ocr=False)
        meta = res if res.get("ok") else {}
    else:
        meta = {}
    return {
        "title": clean_spam_text(meta.get("title") or "").strip(),
        "author": clean_spam_text(meta.get("author") or "").strip(),
        "isbn": (meta.get("isbn") or "").strip(),
    }


def _read_row(row) -> Dict[str, str]:
    """Metadata of one book_files row; empty fields when neither of its paths exists or the file is damaged."""
    empty = {"title": "", "author": "", "isbn": ""}
    # Source folders get cleaned up; the Library hardlink is the same file
    path = next((p for p in (row["original_path"], row["archived_path"]) if p and os.path.exists(p)), "")
    if not path:
        return empty
    try:
        return read_embedded_metadata(path)
    except Exception as e:  # a damaged file must not stop the run; it is recorded as having nothing
        print(f"  unreadable #{row['id']} {os.path.basename(path)[:60]}: {e}", flush=True)
        return empty


def run(limit: int) -> None:
    init_db()
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT f.id, f.original_path, f.archived_path FROM book_files f JOIN books b ON b.id = f.book_id "
            "WHERE COALESCE(b.is_deleted, 0) = 0 AND COALESCE(f.embedded_title, '') = '' "
            "AND f.book_id IN (SELECT book_id FROM book_files GROUP BY book_id HAVING COUNT(*) > 1) ORDER BY f.id"
        ).fetchall()
        if limit:
            rows = rows[:limit]
        started, found = time.time(), 0
        for n, row in enumerate(rows, 1):
            meta = _read_row(row)
            found += bool(meta["title"])
            conn.execute(
                "UPDATE book_files SET embedded_title = ?, embedded_author = ?, isbn = ? WHERE id = ?",
                (meta["title"] or NOTHING_FOUND, meta["author"], meta["isbn"], row["id"]),
            )
            if n % COMMIT_EVERY == 0 or n == len(rows):
                conn.commit()
                rate = n / max(0.1, time.time() - started)
                print(f"[{n}/{len(rows)}] with title {found} | {rate:.1f}/s", flush=True)
    finally:
        conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=0)
    run(ap.parse_args().limit)
