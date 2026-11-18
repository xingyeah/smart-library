import logging
import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from config import LIBRARY_DIR, SOURCE_DIRS, SUPPORTED_EXTENSIONS, is_path_within
from database import get_conn
from enricher import enrich_parsed_book
from parser import parse_ebook_full
from same_work import SAME, file_facts, import_item_facts, judge_same_work


@dataclass(frozen=True)
class ArchiveTarget:
    """Target metadata used to construct library archive paths."""

    title: str
    ext: str
    author: str = ""
    volume_part: str = ""
    edition_tag: str = ""


def sanitize_filename_part(name: str, max_chars: int = 65) -> str:
    if not name:
        return "未知"
    cleaned = name.encode("utf-8", "ignore").decode("utf-8", "ignore")
    cleaned = re.sub(r"[\ufffd\ufff0-\uffff\x00-\x1f\x7f-\x9f\\/:*?\"<>|]+", "_", cleaned.strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
    if not cleaned:
        return "未知"
    return cleaned[:max_chars].strip()


def safe_hardlink_to_library(
    src_path: str,
    target_or_author: Union[ArchiveTarget, str],
    title: str = "",
    volume_part: str = "",
    edition_tag: str = "",
    ext: str = "",
) -> str:
    """Create a zero-copy hardlink in Library/{Author}/{Title}/{Title}.{ext}."""
    if isinstance(target_or_author, ArchiveTarget):
        t = target_or_author
        author, title, volume_part, edition_tag, ext = t.author, t.title, t.volume_part, t.edition_tag, t.ext
    else:
        author = str(target_or_author)

    try:
        safe_author = sanitize_filename_part(author or "未知作者", 45)
        vol_suffix = f" ({volume_part})" if volume_part else ""
        ed_suffix = f" [{edition_tag}]" if edition_tag else ""
        folder_title = sanitize_filename_part(f"{title}{vol_suffix}{ed_suffix}", 70)
        book_dir = LIBRARY_DIR / safe_author / folder_title
        book_dir.mkdir(parents=True, exist_ok=True)

        clean_ext = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
        dst_file = book_dir / f"{folder_title}{clean_ext}"

        if dst_file.exists():
            if os.path.samefile(src_path, str(dst_file)):
                return str(dst_file)
            tag = hashlib.sha1(os.path.abspath(src_path).encode("utf-8")).hexdigest()[:8]
            dst_file = book_dir / f"{folder_title} [{tag}]{clean_ext}"
            if dst_file.exists() and os.path.samefile(src_path, str(dst_file)):
                return str(dst_file)
        os.link(src_path, str(dst_file))
        return str(dst_file)
    except Exception:
        return src_path


def rebuild_vector_cache(reencode_ids=()) -> Dict[str, Any]:
    """Synchronize language/classic scores and 1024-dim BGE-M3 dense vector index."""
    from classic_scorer import backfill_languages_and_classic_scores
    from build_bge_index import sync_bge_m3_index
    import search_engine

    only_ids = set(reencode_ids) if reencode_ids else None
    classic_res = backfill_languages_and_classic_scores(only_ids=only_ids)
    bge_res = sync_bge_m3_index(force_full=False, reencode_ids=set(reencode_ids))
    search_engine.reset_corpus_caches()  # new authors / IDF counts without a restart

    conn = get_conn()
    try:
        conn.execute("UPDATE books SET index_status = 'SYNCED' WHERE index_status != 'SYNCED'")
        conn.commit()
    finally:
        conn.close()
    return {
        "synced_vectors": bge_res.get("total_books", 0),
        "encoded_new": bge_res.get("newly_encoded", 0),
        "classic_updated": classic_res.get("total_books", 0),
    }


def _enrich_new_books(ids: List[int]) -> None:
    """DeepSeek clean title + tags for books this run added; skipped without a key, never fails the run."""
    if not ids:
        return
    try:
        import deepseek_client
        import enrich_ai

        if not deepseek_client.api_key():
            return
        for i in range(0, len(ids), 500):  # stay under SQLite's bound-parameter limit
            enrich_ai.cmd_run(limit=0, ids=ids[i : i + 500], category="", force=False, workers=4)
    except Exception as e:
        print(f"[organize] AI enrichment of new books skipped: {e}", flush=True)


def discover_source_files(
    source_dirs: Optional[List[Dict[str, Any]]] = None,
    limit_per_dir: Optional[int] = None,
) -> List[Tuple[str, str, float]]:
    """Walk configured source directories and return list of (file_path, source_name, trust_weight)."""
    dirs = source_dirs or SOURCE_DIRS
    discovered: List[Tuple[str, str, float]] = []

    for src in dirs:
        root_path, src_name = src["path"], src["name"]
        trust = float(src.get("trust_weight", 1.0))
        if not os.path.exists(root_path):
            continue
        count_in_dir = 0
        for root, dirnames, files in os.walk(root_path):
            dirnames[:] = [d for d in dirnames if not d.startswith((".", "_"))]
            for fn in sorted(files):
                if fn.startswith("."):
                    continue
                ext = os.path.splitext(fn)[1].lower()
                if ext in SUPPORTED_EXTENSIONS:
                    discovered.append((os.path.join(root, fn), src_name, trust))
                    count_in_dir += 1
                    if limit_per_dir and count_in_dir >= limit_per_dir:
                        break
            if limit_per_dir and count_in_dir >= limit_per_dir:
                break
    return discovered


_BOOK_INSERT_SQL = (
    "INSERT INTO books (dedup_key, title, subtitle, norm_title, author, norm_author, "
    "translator, publisher, pub_year, isbn, douban_id, series, volume_part, edition_tag, "
    "category, tags, language, page_count, primary_format, primary_path, archived_path, "
    "file_size, file_hash, source_dir, cover_path, summary, summary_source, core_points, "
    "target_audience, hyde_queries, toc_text, quality_score, pinyin_search, index_status) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'META_READY')"
)

def book_insert_values(item: Dict[str, Any], archived_path: str) -> Tuple[Any, ...]:
    """_BOOK_INSERT_SQL parameters for a parsed and enriched item."""
    text = [item[k] for k in ("dedup_key", "title", "subtitle", "norm_title", "author", "norm_author", "translator",
                              "publisher", "pub_year", "isbn", "douban_id", "series", "volume_part", "edition_tag",
                              "category")]
    return (*text, json.dumps(item["tags"], ensure_ascii=False), item.get("language") or "zh-CN", item["page_count"],
            item["format"].lower(), item["file_path"], archived_path, item["file_size"], item["file_hash"],
            item["source_name"], item["cover_path"], item["summary"], item["summary_source"],
            json.dumps(item["core_points"], ensure_ascii=False), item["target_audience"],
            json.dumps(item["hyde_queries"], ensure_ascii=False), item["toc_text"], item["quality_score"],
            item["pinyin_search"])


_BOOK_UPDATE_SQL = (
    "UPDATE books SET author = CASE WHEN author = '未知作者' AND ? != '未知作者' THEN ? ELSE author END, "
    "norm_author = CASE WHEN norm_author = '未知作者' AND ? != '未知作者' THEN ? ELSE norm_author END, "
    "primary_format = ?, primary_path = ?, archived_path = ?, file_size = ?, file_hash = ?, "
    "cover_path = CASE WHEN ? != '' THEN ? ELSE cover_path END, "
    "summary = CASE WHEN length(?) > length(summary) THEN ? ELSE summary END, "
    "toc_text = CASE WHEN length(?) > length(toc_text) THEN ? ELSE toc_text END, "
    "quality_score = MAX(quality_score, ?), updated_at = CURRENT_TIMESTAMP WHERE id = ?"
)

_BOOK_FILE_INSERT_SQL = (
    "INSERT OR IGNORE INTO book_files (book_id, original_path, archived_path, format, file_size, file_hash, source_name, "
    "is_primary, embedded_title, embedded_author, isbn) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

_CONFLICT_INSERT_SQL = (
    "INSERT OR IGNORE INTO conflict_inbox (file_path, source_name, format, file_size, detected_title, detected_author, "
    "conflict_type, conflict_detail, candidate_book_id, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')"
)


def _record_book_file(
    conn, book_id: int, item: Dict[str, Any], archived_path: str, is_primary: int
) -> None:
    """Record a physical book file occurrence linked to a book entity."""
    conn.execute(
        _BOOK_FILE_INSERT_SQL,
        (
            book_id, item["file_path"], archived_path, item["format"].lower(),
            item["file_size"], item["file_hash"], item["source_name"], is_primary,
            item.get("embedded_title") or "", item.get("embedded_author") or "", item.get("isbn") or "",
        ),
    )


def _log_file_op(conn, run_id: Optional[int], op_type: str, src: str, dst: str, book_id: int) -> None:
    """Audit log file hardlinking or duplication operations."""
    if dst and dst != src:
        conn.execute(
            "INSERT INTO file_ops_log (run_id, op_type, src_path, dst_path, book_id) VALUES (?, ?, ?, ?, ?)",
            (run_id, op_type, src, dst, book_id),
        )


def _check_duplicate_file(conn, item: Dict[str, Any]) -> Tuple[Optional[int], bool]:
    """Check if file path or content hash exists. Returns (book_id, is_deleted)."""
    f = conn.execute(
        "SELECT bf.book_id, b.is_deleted FROM book_files bf JOIN books b ON bf.book_id = b.id "
        "WHERE bf.original_path = ? OR bf.file_hash = ? LIMIT 1",
        (item["file_path"], item["file_hash"]),
    ).fetchone()
    if not f:
        return None, False
    bid, is_del = int(f[0]), bool(f[1])
    if not is_del:
        _record_book_file(conn, bid, item, "", is_primary=0)
    return bid, is_del


def _is_same_work_as_book(conn, book_id: int, item: Dict[str, Any]) -> bool:
    """An uncertain verdict is not a merge: the file goes to the conflict inbox for the user."""
    row = conn.execute("SELECT * FROM book_files WHERE book_id = ? ORDER BY is_primary DESC, id LIMIT 1", (book_id,)).fetchone()
    return row is None or judge_same_work(file_facts(row), import_item_facts(item)) == SAME


def _find_existing_book_or_conflict(conn, item: Dict[str, Any]) -> Optional[Any]:
    """Find existing book matching dedup_key or normalized title with stem compatibility check."""
    existing_book = conn.execute(
        "SELECT * FROM books WHERE dedup_key = ? LIMIT 1", (item["dedup_key"],)
    ).fetchone()
    if existing_book:
        if not _is_same_work_as_book(conn, int(existing_book["id"]), item):
            _record_conflict_entry(conn, item, "stem_mismatch", "与已有书目不是同一作品或无法确定，未合并", int(existing_book["id"]))
            item["dedup_key"] = f"{item['dedup_key']}||{item['file_hash'][:8]}"
            return None
        return existing_book
    if len(item["norm_title"]) < 3:
        return None

    rows = conn.execute(
        "SELECT * FROM books WHERE norm_title = ? AND volume_part = ? AND edition_tag = ?",
        (item["norm_title"], item["volume_part"], item["edition_tag"]),
    ).fetchall()

    for cand in rows:
        ca, na = cand["norm_author"], item["norm_author"]
        if ca in ("未知作者", "未知") or na in ("未知作者", "未知") or ca in na or na in ca:
            if _is_same_work_as_book(conn, int(cand["id"]), item):
                return cand
        _record_conflict_entry(
            conn, item, "ambiguous_duplicate",
            f"同名异书或作者译名差异：当前作者「{item['author']}」 vs 已有库内作者「{cand['author']}」",
            int(cand["id"]),
        )
    return None


def _record_conflict_entry(
    conn, item: Dict[str, Any], conflict_type: str, conflict_detail: str, candidate_book_id: Optional[int] = None
) -> None:
    """Record an unmerged or ambiguous entry into conflict_inbox for manual audit."""
    conn.execute(
        _CONFLICT_INSERT_SQL,
        (
            item["file_path"], item["source_name"], item["format"].lower(),
            item["file_size"], item["title"], item["author"],
            conflict_type, conflict_detail, candidate_book_id,
        ),
    )


def _merge_existing_book(
    conn, item: Dict[str, Any], existing_book: Any, archived_path: str, run_id: Optional[int]
) -> Dict[str, Any]:
    """Merge an alternate format or upgrade an existing book record."""
    book_id = int(existing_book["id"])
    fmt = item["format"].lower()
    fmt_priority = SUPPORTED_EXTENSIONS.get(f".{fmt}", 40)
    old_fmt = (existing_book["primary_format"] or "").lower()
    old_priority = SUPPORTED_EXTENSIONS.get(f".{old_fmt}", 40)
    is_better = fmt_priority > old_priority or (
        fmt_priority == old_priority and item["quality_score"] > float(existing_book["quality_score"] or 0)
    )

    if is_better:
        conn.execute("UPDATE book_files SET is_primary = 0 WHERE book_id = ?", (book_id,))
        conn.execute(
            _BOOK_UPDATE_SQL,
            (
                item["author"], item["author"], item["norm_author"], item["norm_author"],
                fmt, item["file_path"], archived_path, item["file_size"], item["file_hash"],
                item["cover_path"], item["cover_path"], item["summary"], item["summary"],
                item["toc_text"], item["toc_text"], item["quality_score"], book_id,
            ),
        )

    _record_book_file(conn, book_id, item, archived_path, 1 if is_better else 0)
    _log_file_op(conn, run_id, "duplicate_link", item["file_path"], archived_path, book_id)
    return {"action": "merged_format", "book_id": book_id}


def _detect_metadata_conflict(item: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """Detect low confidence titles or unparsed author metadata."""
    if re.match(r"^[0-9_\-\s\.]+$", item["title"]) or len(item["norm_title"]) <= 1:
        return "corrupted_title", f"书名疑似残缺或纯编号：「{item['title']}」，建议核对原文件补全书名"
    if item["author"] == "未知作者" and not item["isbn"]:
        return "missing_author", "未能从内嵌元数据或文件名解析出作者，当前归入「未知作者」"
    return None, ""


def _insert_new_book(
    conn, item: Dict[str, Any], archived_path: str, run_id: Optional[int]
) -> Dict[str, Any]:
    """Insert a new canonical book record, primary file, and audit entries."""
    cur = conn.execute(_BOOK_INSERT_SQL, book_insert_values(item, archived_path))
    book_id = int(cur.lastrowid)
    _record_book_file(conn, book_id, item, archived_path, 1)
    _log_file_op(conn, run_id, "hardlink_archive", item["file_path"], archived_path, book_id)

    conflict_type, conflict_detail = _detect_metadata_conflict(item)
    if conflict_type:
        _record_conflict_entry(conn, item, conflict_type, conflict_detail, book_id)

    return {"action": "inserted", "book_id": book_id, "conflict": conflict_type is not None}


def upsert_enriched_book(
    conn,
    item: Dict[str, Any],
    run_id: Optional[int] = None,
    do_archive_link: bool = True,
) -> Dict[str, Any]:
    """Insert or merge a parsed & enriched book into SQLite, archiving hardlinks."""
    dup_book_id, is_deleted = _check_duplicate_file(conn, item)
    if is_deleted:
        return {"action": "skipped_deleted", "book_id": dup_book_id}
    if dup_book_id is not None:
        return {"action": "duplicate_hash", "book_id": dup_book_id}

    existing_book = _find_existing_book_or_conflict(conn, item)
    if existing_book and int(existing_book["is_deleted"] or 0) == 1:
        return {"action": "skipped_deleted", "book_id": int(existing_book["id"])}

    archived_path = ""
    if do_archive_link:
        target = ArchiveTarget(
            title=item["title"],
            ext=item["format"],
            author=item["author"],
            volume_part=item["volume_part"],
            edition_tag=item["edition_tag"],
        )
        archived_path = safe_hardlink_to_library(item["file_path"], target)

    if existing_book:
        return _merge_existing_book(conn, item, existing_book, archived_path, run_id)
    return _insert_new_book(conn, item, archived_path, run_id)


def _update_run_stats(
    conn, run_id: int, stats: Dict[str, Any], status: Optional[str] = None, detail_json: Optional[str] = None
) -> None:
    """Persist pipeline progress and completion counters to organize_runs."""
    cols = ["scanned_files", "indexed_books", "archived_links", "duplicates_merged", "conflicts_found", "ocr_triggered", "summary_generated"]
    clause = ", ".join(f"{c} = ?" for c in cols)
    params = [stats[c] for c in cols]
    if status:
        clause += ", status = ?, finished_at = CURRENT_TIMESTAMP"
        params.append(status)
    if detail_json:
        clause += ", detail_json = ?"
        params.append(detail_json)
    params.append(run_id)
    conn.execute(f"UPDATE organize_runs SET {clause} WHERE id = ?", tuple(params))


def run_organize_pipeline(
    limit_per_dir: Optional[int] = None,
    run_type: str = "manual",
    enable_ocr: bool = False,
    max_workers: int = 6,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Scan sources, parse & enrich books in parallel, archive via hardlinks, update indices."""
    conn = get_conn()
    cur = conn.execute("INSERT INTO organize_runs (run_type, status) VALUES (?, 'running')", (run_type,))
    run_id = int(cur.lastrowid)
    conn.commit()
    existing_paths = {r["original_path"] for r in conn.execute("SELECT original_path FROM book_files").fetchall()}
    conn.close()

    all_discovered = discover_source_files(limit_per_dir=limit_per_dir)
    pending_files = [t for t in all_discovered if t[0] not in existing_paths]
    stats = {
        "run_id": run_id, "total_discovered": len(all_discovered),
        "already_indexed": len(all_discovered) - len(pending_files),
        "scanned_files": 0, "indexed_books": 0, "archived_links": 0,
        "duplicates_merged": 0, "conflicts_found": 0, "ocr_triggered": 0, "summary_generated": 0,
    }

    if not pending_files:
        vec_info = rebuild_vector_cache()
        conn = get_conn()
        _update_run_stats(conn, run_id, stats, "completed", json.dumps({**stats, **vec_info}, ensure_ascii=False))
        conn.commit()
        conn.close()
        return {**stats, **vec_info}

    def _worker(task: Tuple[str, str, float]) -> Optional[Dict[str, Any]]:
        try:
            return enrich_parsed_book(parse_ebook_full(task[0], source_name=task[1], enable_ocr=enable_ocr), trust_weight=task[2])
        except Exception:
            return None

    batch_buffer: List[Dict[str, Any]] = []
    new_ids: List[int] = []

    def _flush_batch(items: List[Dict[str, Any]]) -> None:
        if not items:
            return
        c = get_conn()
        try:
            for it in items:
                res = upsert_enriched_book(c, it, run_id=run_id, do_archive_link=True)
                stats["scanned_files"] += 1
                if it.get("used_ocr"):
                    stats["ocr_triggered"] += 1
                if it.get("summary"):
                    stats["summary_generated"] += 1
                if res["action"] == "inserted":
                    new_ids.append(res["book_id"])
                    stats["indexed_books"] += 1
                    stats["archived_links"] += 1
                    if res.get("conflict"):
                        stats["conflicts_found"] += 1
                elif res["action"] in ("merged_format", "duplicate_hash"):
                    stats["duplicates_merged"] += 1
            _update_run_stats(c, run_id, stats)
            c.commit()
        finally:
            c.close()
        if progress_callback:
            progress_callback(stats)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_worker, t) for t in pending_files]
        for fut in as_completed(futures):
            item = fut.result()
            if item:
                batch_buffer.append(item)
                if len(batch_buffer) >= 80:
                    _flush_batch(batch_buffer)
                    batch_buffer.clear()

    if batch_buffer:
        _flush_batch(batch_buffer)
        batch_buffer.clear()

    _enrich_new_books(new_ids)
    vec_info = rebuild_vector_cache()
    conn = get_conn()
    _update_run_stats(conn, run_id, stats, "completed", json.dumps({**stats, **vec_info}, ensure_ascii=False))
    conn.commit()
    conn.close()
    return {**stats, **vec_info}


def rollback_organize_run(run_id: int) -> Dict[str, Any]:
    """Safely remove hardlinks created in a specific organize run without touching source files."""
    conn = get_conn()
    try:
        ops = conn.execute("SELECT id, src_path, dst_path FROM file_ops_log WHERE run_id = ? AND rolled_back = 0", (run_id,)).fetchall()
        removed = kept = 0
        for op in ops:
            dst, src = op["dst_path"], op["src_path"]
            if dst and dst != src and os.path.exists(dst) and is_path_within(dst, LIBRARY_DIR):
                if os.stat(dst).st_nlink < 2:
                    kept += 1
                    continue
                try:
                    os.unlink(dst)
                    removed += 1
                except OSError:
                    # Unlinking temporary hardlink may fail if file was removed or permissions restricted
                    logging.getLogger(__name__).debug("could not remove temporary hardlink", exc_info=True)
            conn.execute("UPDATE file_ops_log SET rolled_back = 1 WHERE id = ?", (op["id"],))
        conn.commit()
        return {"run_id": run_id, "unlinked_files": removed, "kept_last_copies": kept}
    finally:
        conn.close()
