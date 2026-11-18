"""
Find and split records that hold files of more than one work (同书归并与误合并拆分方案.md, section 4).

Verdicts come from same_work plus the user's own per-file decisions (data/split_decisions.json). A split keeps
the anchor file on the original record, so favourites, reading progress and AI metadata stay with the book the
user knows, and creates one record per other work. Every change is logged under one organize_runs row of type
'split'; split_undo.undo_split_run reverses a whole run.
"""

import json
import logging
import os
import re
import threading
from typing import Any, Dict, List, Mapping, Optional, Tuple

from archiver import (
    ArchiveTarget, _BOOK_INSERT_SQL, _enrich_new_books, _is_same_work_as_book, book_insert_values,
    safe_hardlink_to_library,
)
from config import DATA_DIR, LIBRARY_DIR
from database import backup_database, get_conn
from ebook_parser.filenames import clean_spam_text, english_title_from_path, normalize_for_dedup
from ebook_parser.pipeline import parse_ebook
from enricher import enrich_parsed_book
from enrichers.base import search_title
from same_work import DIFFERENT, SAME, UNCERTAIN, file_facts, is_placeholder_title, judge_same_work, plan_book_split

logger = logging.getLogger("mismerge_service")

DECISIONS_PATH = DATA_DIR / "split_decisions.json"
VERDICTS = (SAME, DIFFERENT, UNCERTAIN)
SPLIT_RUN_TYPE = "split"
UNKNOWN_AUTHOR = "未知作者"
_WORD_EXPORT_NOISE = re.compile(r"^Microsoft Word - |\.(?:docx?|dot)$", re.I)  # PDFs printed from Word


class SplitError(Exception):
    """A record could not be split; nothing of it was changed."""


def load_decisions() -> Dict[int, str]:
    """The user's verdicts from reviewing a preview, {book_files.id: same|different|uncertain}; they beat the rules."""
    if not DECISIONS_PATH.exists():
        return {}
    raw = json.loads(DECISIONS_PATH.read_text(encoding="utf-8"))
    bad = {k: v for k, v in raw.items() if v not in VERDICTS or not str(k).isdigit()}
    if bad:
        raise ValueError(f"{DECISIONS_PATH}: unknown file id or verdict {bad}")
    return {int(k): v for k, v in raw.items()}


def apply_decisions(plan: Dict[str, Any], decisions: Mapping[int, str]) -> None:
    """Move each file the user decided on into the bucket they chose (the anchor stays)."""
    for verdict in VERDICTS:
        for file_id in [i for i in plan[verdict] if decisions.get(i, verdict) != verdict]:
            plan[verdict].remove(file_id)
            plan[decisions[file_id]].append(file_id)


def group_new_books(rows: List[Mapping[str, Any]]) -> List[List[int]]:
    """Files split off one record, grouped into the books they would become: a file joins the first group whose
    first file it is the same work as, otherwise starts its own."""
    groups: List[List[Any]] = []
    for fact in (file_facts(r) for r in rows):
        home = next((g for g in groups if judge_same_work(g[0], fact) == SAME), None)
        if home is None:
            groups.append([fact])
        else:
            home.append(fact)
    return [[f.file_id for f in g] for g in groups]


def preview_book(rows: List[Mapping[str, Any]], book_title: str, decisions: Mapping[int, str]) -> Dict[str, Any]:
    """plan_book_split with the user's decisions applied, plus `new_books`: the split-off file ids grouped into
    the books they would form."""
    plan = plan_book_split(rows, book_title)
    apply_decisions(plan, decisions)
    split_rows = [r for r in rows if r["id"] in set(plan[DIFFERENT])]
    return {**plan, "new_books": group_new_books(split_rows)}


def _book_files(conn, book_id: int) -> List[Dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM book_files WHERE book_id = ? ORDER BY id", (book_id,))]


def _file_name(row: Mapping[str, Any]) -> str:
    return os.path.basename(row["original_path"] or row["archived_path"] or "")


def _describe_split(book: Mapping[str, Any], rows: List[Dict[str, Any]], preview: Dict[str, Any]) -> Dict[str, Any]:
    """The shape the 误合并 panel renders: the group that stays first, then one group per new book."""
    by_id = {r["id"]: r for r in rows}
    kept = [preview["anchor"], *preview[SAME], *preview[UNCERTAIN]]
    groups = [kept, *preview["new_books"]]
    return {
        "book_id": book["id"], "title": book["title"], "author": book["author"], "category": book["category"],
        "file_count": len(rows), "distinct_groups_count": len(groups),
        "groups": [
            {"group_index": n, "is_retained_in_book": n == 0,
             "files": [{"id": i, "filename": _file_name(by_id[i]), "is_primary": by_id[i]["is_primary"],
                        "size": by_id[i]["file_size"]} for i in group]}
            for n, group in enumerate(groups)
        ],
    }


def detect_mismerged_books(conn=None, limit: int = 100) -> List[Dict[str, Any]]:
    """Up to `limit` records that the rules (and the user's decisions) would split."""
    own_conn = conn is None
    conn = conn or get_conn()
    try:
        decisions = load_decisions()
        books = conn.execute(
            "SELECT id, title, author, category FROM books WHERE COALESCE(is_deleted, 0) = 0 "
            "AND id IN (SELECT book_id FROM book_files GROUP BY book_id HAVING COUNT(*) > 1) ORDER BY id"
        ).fetchall()
        results: List[Dict[str, Any]] = []
        for book in books:
            rows = _book_files(conn, book["id"])
            preview = preview_book(rows, book["title"], decisions)
            if preview["new_books"]:
                results.append(_describe_split(book, rows, preview))
            if len(results) >= limit:
                break
        return results
    finally:
        if own_conn:
            conn.close()


def choose_title(row: Mapping[str, Any], fallback: str) -> Tuple[str, bool]:
    """Title for a record made from this file: the title inside the file, then the English file-name title.
    File names often carry a made-up batch-rename name, so `fallback` is used only when both are unusable;
    the second value says the record still needs a real title."""
    path = row["original_path"] or row["archived_path"] or ""
    for candidate in (row["embedded_title"], english_title_from_path(path)):
        title = clean_spam_text(candidate or "").strip()
        if title and not is_placeholder_title(title):
            return title, False
    return _WORD_EXPORT_NOISE.sub("", fallback).strip() or fallback, True


def _existing_path(row: Mapping[str, Any]) -> str:
    """The file on disk: its source, or the Library link when the source folder was cleaned up."""
    path = next((p for p in (row["original_path"], row["archived_path"]) if p and os.path.exists(p)), "")
    if not path:
        raise SplitError(f"file #{row['id']} not found: {row['original_path']}")
    return path


def _log(conn, run_id: int, entry: Tuple[str, int, str, str]) -> None:
    op_type, book_id, src, dst = entry
    conn.execute(
        "INSERT INTO file_ops_log (run_id, op_type, src_path, dst_path, book_id) VALUES (?, ?, ?, ?, ?)",
        (run_id, op_type, src, dst, book_id),
    )


def _link(path: str, item: Mapping[str, Any]) -> str:
    target = ArchiveTarget(title=item["title"], ext=item["format"], author=item["author"],
                           volume_part=item.get("volume_part") or "", edition_tag=item.get("edition_tag") or "")
    link = safe_hardlink_to_library(path, target)
    return link if link != path else ""  # the helper hands back the source when linking failed


def _new_book_item(lead: Mapping[str, Any], path: str) -> Tuple[Dict[str, Any], bool]:
    """Parsed, titled and enriched (category, summary, search tokens) like a freshly imported book."""
    parsed = parse_ebook(path, source_name=lead["source_name"] or "")
    title, needs_title = choose_title(lead, parsed["title"])
    author = next(iter(file_facts(lead).authors), "") or parsed["author"] or UNKNOWN_AUTHOR
    norm_title, norm_author = normalize_for_dedup(title, author)
    parsed.update(title=title, author=author, norm_title=norm_title, norm_author=norm_author,
                  dedup_key=f"{norm_title}||{norm_author}||{parsed['volume_part']}||{parsed['edition_tag']}")
    item = enrich_parsed_book(parsed)
    item["summary_source"] = item.get("summary_source") or "split_mismerged"
    return item, needs_title


def _same_key_record(conn, item: Dict[str, Any], original_id: int) -> Optional[int]:
    """The live record this work already has elsewhere in the library, if any. A key held by another work, a
    deleted record or the record being split gets this file's hash appended, as dedup_key is unique."""
    holder = conn.execute("SELECT id, is_deleted FROM books WHERE dedup_key = ?", (item["dedup_key"],)).fetchone()
    if not holder:
        return None
    usable = not holder["is_deleted"] and holder["id"] != original_id
    if usable and _is_same_work_as_book(conn, int(holder["id"]), item):
        return int(holder["id"])
    item["dedup_key"] = f"{item['dedup_key']}||{item['file_hash'][:8]}"
    return None


def _join_existing_book(conn, group: List[Dict[str, Any]], book_id: int, origin: Tuple[int, int]) -> Dict[str, Any]:
    """The split-off files join the record their work already has, as extra copies."""
    run_id, original_id = origin
    for row in group:
        conn.execute("UPDATE book_files SET book_id = ?, is_primary = 0 WHERE id = ?", (book_id, row["id"]))
    _log(conn, run_id, ("split_join", book_id, str(original_id), ""))
    title = conn.execute("SELECT title, author FROM books WHERE id = ?", (book_id,)).fetchone()
    return {"new_book_id": None, "joined_book_id": book_id, "title": title["title"], "author": title["author"],
            "file_count": len(group), "needs_title": False}


def _create_book_for_group(conn, group: List[Dict[str, Any]], origin: Tuple[int, int]) -> Dict[str, Any]:
    """A new record for one split-off work; its largest file (the old primary if it is here) leads."""
    run_id, original_id = origin
    lead = max(group, key=lambda r: (r["is_primary"] or 0, r["file_size"] or 0))
    path = _existing_path(lead)
    item, needs_title = _new_book_item(lead, path)
    holder = _same_key_record(conn, item, original_id)
    if holder:
        return _join_existing_book(conn, group, holder, origin)
    link = _link(path, item)
    if link and path == lead["archived_path"]:
        item["file_path"] = link  # the source is gone and its old link is dropped once superseded
    new_id = int(conn.execute(_BOOK_INSERT_SQL, book_insert_values(item, link)).lastrowid)
    for row in group:
        is_lead = row["id"] == lead["id"]
        conn.execute("UPDATE book_files SET book_id = ?, is_primary = ?, archived_path = ? WHERE id = ?",
                     (new_id, int(is_lead), link if is_lead and link else row["archived_path"], row["id"]))
    _log(conn, run_id, ("split_new_book", new_id, str(original_id), link))
    if link and lead["archived_path"] and lead["archived_path"] != link:
        _log(conn, run_id, ("split_unlink", new_id, lead["archived_path"], link))  # removed after commit
    return {"new_book_id": new_id, "title": item["title"], "author": item["author"], "file_count": len(group),
            "needs_title": needs_title}


_BOOK_STATE_COLUMNS = ("title", "norm_title", "dedup_key", "primary_path", "archived_path", "primary_format",
                       "file_size", "file_hash")


def _snapshot(book: Mapping[str, Any], rows: List[Dict[str, Any]]) -> str:
    """Everything a split changes on the original record and its files, for undo."""
    return json.dumps({
        "book": {c: book[c] for c in _BOOK_STATE_COLUMNS},
        "files": [[r["id"], r["is_primary"], r["archived_path"]] for r in rows],
    }, ensure_ascii=False)


def _promote_anchor(conn, book: Mapping[str, Any], anchor: Mapping[str, Any], run_id: int) -> Dict[str, Any]:
    """The primary file left with a split-off work: the anchor becomes primary, with a Library link."""
    path = _existing_path(anchor)
    link = anchor["archived_path"]
    if not link:
        link = _link(path, {"title": book["title"], "format": anchor["format"], "author": book["author"],
                            "volume_part": book["volume_part"], "edition_tag": book["edition_tag"]})
        _log(conn, run_id, ("split_link", book["id"], path, link))
    conn.execute(
        "UPDATE book_files SET is_primary = CASE WHEN id = ? THEN 1 ELSE 0 END, "
        "archived_path = CASE WHEN id = ? THEN ? ELSE archived_path END WHERE book_id = ?",
        (anchor["id"], anchor["id"], link, book["id"]),
    )
    return {"primary_path": path, "archived_path": link, "primary_format": (anchor["format"] or "").lower(),
            "file_size": anchor["file_size"] or 0, "file_hash": anchor["file_hash"] or ""}


def _retitle_placeholder(book: Mapping[str, Any], anchor: Mapping[str, Any]) -> Dict[str, Any]:
    """'未知之书 (51cbd00882e7d)', 'Microsoft Word - 文档 1': name the record after its anchor file instead."""
    if not is_placeholder_title(search_title(book["title"])):
        return {}
    title, needs_title = choose_title(anchor, "")
    if needs_title:
        return {}
    norm_title, norm_author = normalize_for_dedup(title, book["author"])
    key = f"{norm_title}||{norm_author}||{book['volume_part'] or ''}||{book['edition_tag'] or ''}"
    return {"title": title, "norm_title": norm_title, "dedup_key": key}


def _refresh_original_book(conn, book: Mapping[str, Any], anchor: Mapping[str, Any], run_id: int) -> None:
    updates: Dict[str, Any] = {}
    if not any(r["is_primary"] for r in _book_files(conn, book["id"])):
        updates.update(_promote_anchor(conn, book, anchor, run_id))
    updates.update(_retitle_placeholder(book, anchor))
    if updates:
        assignments = ", ".join(f"{column} = ?" for column in updates)
        conn.execute(f"UPDATE books SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (*updates.values(), book["id"]))


def _split_in_transaction(conn, book_id: int, run_id: int) -> Dict[str, Any]:
    book = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if not book or book["is_deleted"]:
        raise SplitError(f"Book #{book_id} not found or deleted")
    rows = _book_files(conn, book_id)
    preview = preview_book(rows, book["title"], load_decisions())
    if not preview["new_books"]:
        return {"ok": True, "original_book_id": book_id, "created_books": [], "message": "nothing to split"}
    by_id = {r["id"]: r for r in rows}
    _log(conn, run_id, ("split_origin", book_id, _snapshot(book, rows), ""))
    created = [_create_book_for_group(conn, [by_id[i] for i in group], (run_id, book_id))
               for group in preview["new_books"]]
    _refresh_original_book(conn, book, by_id[preview["anchor"]], run_id)
    return {"ok": True, "run_id": run_id, "original_book_id": book_id, "created_books": created,
            "affected_ids": [book_id, *(c["new_book_id"] or c["joined_book_id"] for c in created)]}


def _start_run(status: str) -> int:
    conn = get_conn()
    try:
        run_id = int(conn.execute("INSERT INTO organize_runs (run_type, status) VALUES (?, ?)",
                                  (SPLIT_RUN_TYPE, status)).lastrowid)
        conn.commit()
        return run_id
    finally:
        conn.close()


def _split_and_commit(book_id: int, run_id: int) -> Dict[str, Any]:
    """One record in one transaction: on any error nothing of it changes."""
    conn = get_conn()
    try:
        result = _split_in_transaction(conn, book_id, run_id)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _drop_replaced_links(run_id: int) -> None:
    """Library links superseded by a split-off book's own link: removed once nothing refers to them and the
    new link is the same file, so no content is ever lost."""
    from book_removal_service import _cleanup_empty_parent_dirs

    conn = get_conn()
    try:
        for op in conn.execute("SELECT src_path, dst_path FROM file_ops_log WHERE run_id = ? AND op_type = "
                               "'split_unlink' AND rolled_back = 0", (run_id,)).fetchall():
            old, new = op["src_path"], op["dst_path"]
            referenced = conn.execute("SELECT 1 FROM books WHERE archived_path = ? UNION SELECT 1 FROM book_files "
                                      "WHERE archived_path = ?", (old, old)).fetchone()
            safe = old.startswith(str(LIBRARY_DIR)) and os.path.exists(old) and os.path.exists(new)
            if safe and not referenced and os.path.samefile(old, new):
                os.unlink(old)
                _cleanup_empty_parent_dirs(old, LIBRARY_DIR)
    finally:
        conn.close()


def _after_split(run_id: int, affected_ids: List[int], new_ids: List[int]) -> None:
    """Off the request thread: drop superseded links, re-encode vectors, AI title and tags for new books."""
    from http_api.background import reindex_in_background

    def work() -> None:
        _drop_replaced_links(run_id)
        reindex_in_background(affected_ids)
        _enrich_new_books(new_ids)

    threading.Thread(target=work, daemon=True).start()


def split_mismerged_book(book_id: int) -> Dict[str, Any]:
    """Split one record now (its own undoable run). Errors other than a missing file propagate."""
    run_id = _start_run("done")
    try:
        result = _split_and_commit(book_id, run_id)
    except SplitError as e:
        return {"ok": False, "error": str(e)}
    created = result["created_books"]
    if created:
        _after_split(run_id, result["affected_ids"], [c["new_book_id"] for c in created if c["new_book_id"]])
    return result


def _finish_run(run_id: int, summary: Dict[str, Any]) -> None:
    conn = get_conn()
    try:
        conn.execute("UPDATE organize_runs SET status = 'done', detail_json = ?, finished_at = CURRENT_TIMESTAMP "
                     "WHERE id = ?", (json.dumps(summary, ensure_ascii=False), run_id))
        conn.commit()
    finally:
        conn.close()


def batch_split_mismerged(limit: int = 50) -> Dict[str, Any]:
    """Back up the database, then split up to `limit` detected records as one undoable run."""
    backup = str(backup_database())
    run_id = _start_run("running")
    details: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    for item in detect_mismerged_books(limit=limit):
        try:
            res = _split_and_commit(item["book_id"], run_id)
            details.append({"book_id": item["book_id"], "created_books": res["created_books"]})
        except Exception as e:  # one bad file must not stop the batch; its record stays untouched
            logger.exception("Split of book #%s failed", item["book_id"])
            failures.append({"book_id": item["book_id"], "error": str(e)})
    new_ids = [c["new_book_id"] for d in details for c in d["created_books"] if c["new_book_id"]]
    summary = {"split_books": len(details), "new_books": len(new_ids), "failures": failures, "backup": backup}
    _finish_run(run_id, summary)
    if new_ids:
        affected = {d["book_id"] for d in details}
        affected |= {c["new_book_id"] or c["joined_book_id"] for d in details for c in d["created_books"]}
        _after_split(run_id, sorted(affected), new_ids)
    return {"ok": True, "run_id": run_id, "backup": backup, "split_success_count": len(details),
            "total_new_books_created": len(new_ids), "details": details, "failures": failures}
