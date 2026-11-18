"""Bidirectional catalog sync for accurate book summaries (CSV + XLSX).

Writes `ai_refreshed` and `manual` synopses back into `电子书分类索引与简介目录.csv`
and `电子书分类索引与简介目录.xlsx`, tagging `简介来源` and `简介更新时间` so
subsequent catalog imports never overwrite curated AI/manual descriptions.
"""

import csv
import logging
import os
import re
import threading
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape as xml_escape

from config import BASE_DIR, DB_PATH
from database import get_conn

logger = logging.getLogger("catalog_sync")

PROTECTED_SUMMARY_SOURCES = ("ai_refreshed", "manual")
CATALOG_CSV_PATH = BASE_DIR / "电子书分类索引与简介目录.csv"
CATALOG_XLSX_PATH = BASE_DIR / "电子书分类索引与简介目录.xlsx"
CATALOG_FIELDS = (
    "全局序号",
    "分类编号",
    "分类名称",
    "电子书文件名",
    "文件格式",
    "出版年份",
    "书库来源",
    "核心内容简要介绍",
    "简介来源",
    "简介更新时间",
)
SYNC_DEBOUNCE_SEC = 5.0

_sync_lock = threading.RLock()
_debounce_lock = threading.Lock()
_pending_timer: Optional[threading.Timer] = None


def _col_letter(idx_one_based: int) -> str:
    letters = []
    n = idx_one_based
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters.append(chr(65 + rem))
    return "".join(reversed(letters))


def _sanitize_xml_text(text: str) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(text or ""))


def _build_sheet_xml(rows: List[Dict[str, str]]) -> bytes:
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        "<sheetData>",
    ]
    all_rows: List[Tuple[str, ...]] = [CATALOG_FIELDS]
    for row in rows:
        all_rows.append(tuple(row.get(col, "") for col in CATALOG_FIELDS))

    for r_idx, values in enumerate(all_rows, start=1):
        cells = []
        for c_idx, val in enumerate(values, start=1):
            ref = f"{_col_letter(c_idx)}{r_idx}"
            safe = xml_escape(_sanitize_xml_text(val))
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{safe}</t></is></c>')
        parts.append(f'<row r="{r_idx}">{"".join(cells)}</row>')

    parts.append("</sheetData></worksheet>")
    return "".join(parts).encode("utf-8")


def write_minimal_xlsx(rows: List[Dict[str, str]], xlsx_path: Path) -> None:
    """Write rows into a minimal valid single-sheet XLSX using Python stdlib only."""
    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = xlsx_path.with_name(f".{xlsx_path.name}.{os.getpid()}.tmp")
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="电子书目录" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )
    wb_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )
    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", content_types)
            zf.writestr("_rels/.rels", root_rels)
            zf.writestr("xl/workbook.xml", workbook_xml)
            zf.writestr("xl/_rels/workbook.xml.rels", wb_rels)
            zf.writestr("xl/worksheets/sheet1.xml", _build_sheet_xml(rows))
        os.replace(tmp_path, xlsx_path)
    except BaseException:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def _collect_book_filenames(conn: Any) -> Dict[int, List[str]]:
    file_rows = conn.execute(
        "SELECT book_id, original_path, archived_path FROM book_files ORDER BY is_primary DESC, id ASC"
    ).fetchall()
    names_by_book: Dict[int, List[str]] = {}
    for fr in file_rows:
        bid = int(fr["book_id"])
        for col in ("original_path", "archived_path"):
            p = (fr[col] or "").strip()
            if p:
                names_by_book.setdefault(bid, []).append(os.path.basename(p))
    return names_by_book


def _load_protected_books(conn: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    names_by_book = _collect_book_filenames(conn)
    ph = ",".join("?" for _ in PROTECTED_SUMMARY_SOURCES)
    book_rows = conn.execute(
        f"""
        SELECT id, category, primary_format, pub_year, source_dir,
               summary, summary_source, updated_at, primary_path, archived_path
        FROM books
        WHERE is_deleted = 0
          AND summary_source IN ({ph})
          AND TRIM(COALESCE(summary, '')) != ''
        ORDER BY id ASC
        """,
        PROTECTED_SUMMARY_SOURCES,
    ).fetchall()

    books_list: List[Dict[str, Any]] = []
    by_name: Dict[str, Dict[str, Any]] = {}
    for r in book_rows:
        bid = int(r["id"])
        candidates = list(names_by_book.get(bid, [])) + [
            os.path.basename((r[c] or "").strip()) for c in ("primary_path", "archived_path") if (r[c] or "").strip()
        ]
        dedup_names = list(dict.fromkeys(c for c in candidates if c))
        if not dedup_names:
            continue
        item = {
            "id": bid,
            "category": (r["category"] or "综合通识").strip(),
            "format": (r["primary_format"] or "").strip().upper(),
            "pub_year": (r["pub_year"] or "").strip(),
            "source_dir": (r["source_dir"] or "").strip(),
            "summary": (r["summary"] or "").strip(),
            "summary_source": (r["summary_source"] or "").strip(),
            "updated_at": (r["updated_at"] or "").strip(),
            "primary_name": dedup_names[0],
        }
        books_list.append(item)
        for name in dedup_names:
            by_name.setdefault(name, item)
            by_name.setdefault(name.lower(), item)
    return books_list, by_name


def _read_existing_csv(csv_path: Path) -> List[Dict[str, str]]:
    if not csv_path.exists():
        return []
    rows: List[Dict[str, str]] = []
    with open(csv_path, "r", encoding="utf-8-sig") as fp:
        for raw in csv.DictReader(fp):
            fname = (raw.get("电子书文件名") or raw.get("电子书文件名（规范化）") or raw.get("文件名") or "").strip()
            if not fname:
                continue
            rows.append(
                {
                    "全局序号": (raw.get("全局序号") or "").strip(),
                    "分类编号": (raw.get("分类编号") or "").strip(),
                    "分类名称": (raw.get("分类名称") or "").strip(),
                    "电子书文件名": fname,
                    "文件格式": (raw.get("文件格式") or "").strip(),
                    "出版年份": (raw.get("出版年份") or "").strip(),
                    "书库来源": (raw.get("书库来源") or "").strip(),
                    "核心内容简要介绍": (raw.get("核心内容简要介绍") or raw.get("简要内容介绍（精准学术）") or raw.get("简介") or "").strip(),
                    "简介来源": (raw.get("简介来源") or "").strip(),
                    "简介更新时间": (raw.get("简介更新时间") or "").strip(),
                }
            )
    return rows


def _write_csv_atomic(rows: List[Dict[str, str]], csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = csv_path.with_name(f".{csv_path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8-sig", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=CATALOG_FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow({col: row.get(col, "") for col in CATALOG_FIELDS})
        os.replace(tmp_path, csv_path)
    except BaseException:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def _merge_protected_into_rows(
    rows: List[Dict[str, str]],
    books_list: List[Dict[str, Any]],
    by_name: Dict[str, Dict[str, Any]],
) -> Tuple[int, int]:
    cat_code_map: Dict[str, str] = {}
    max_seq = 0
    for row in rows:
        if row["分类名称"] and row["分类编号"]:
            cat_code_map.setdefault(row["分类名称"], row["分类编号"])
        if row["全局序号"].isdigit():
            max_seq = max(max_seq, int(row["全局序号"]))

    matched_ids = set()
    updated_rows = 0
    for row in rows:
        fname = row["电子书文件名"]
        book = by_name.get(fname) or by_name.get(fname.lower())
        if not book:
            if not row["简介来源"] and row["核心内容简要介绍"]:
                row["简介来源"] = "xlsx_curated"
            continue
        matched_ids.add(book["id"])
        row["核心内容简要介绍"] = book["summary"]
        row["分类名称"] = book["category"]
        row["分类编号"] = cat_code_map.get(book["category"], row["分类编号"])
        row["简介来源"] = book["summary_source"]
        row["简介更新时间"] = book["updated_at"]
        updated_rows += 1

    appended_rows = 0
    for book in books_list:
        if book["id"] in matched_ids:
            continue
        max_seq += 1
        appended_rows += 1
        rows.append({
            "全局序号": str(max_seq), "分类编号": cat_code_map.get(book["category"], "00"),
            "分类名称": book["category"], "电子书文件名": book["primary_name"],
            "文件格式": book["format"], "出版年份": book["pub_year"],
            "书库来源": book["source_dir"], "核心内容简要介绍": book["summary"],
            "简介来源": book["summary_source"], "简介更新时间": book["updated_at"],
        })
    return updated_rows, appended_rows


def sync_accurate_summaries_to_catalog(
    db_path: Path = DB_PATH,
    csv_path: Optional[Path] = None,
    xlsx_path: Optional[Path] = None,
) -> Dict[str, int]:
    """Write all protected (`ai_refreshed`/`manual`) DB synopses into CSV and XLSX catalogs."""
    target_csv = csv_path or CATALOG_CSV_PATH
    target_xlsx = xlsx_path or CATALOG_XLSX_PATH

    with _sync_lock:
        conn = get_conn(db_path)
        try:
            books_list, by_name = _load_protected_books(conn)
        finally:
            conn.close()

        rows = _read_existing_csv(target_csv)
        updated_rows, appended_rows = _merge_protected_into_rows(rows, books_list, by_name)
        _write_csv_atomic(rows, target_csv)
        write_minimal_xlsx(rows, target_xlsx)
        return {"updated_rows": updated_rows, "appended_rows": appended_rows, "total_rows": len(rows)}


def _run_debounced_sync(db_path: Path) -> None:
    try:
        res = sync_accurate_summaries_to_catalog(db_path=db_path)
        logger.info("Catalog sync completed: %s", res)
    except Exception:
        logger.exception("Background catalog sync failed")


def enqueue_catalog_sync(db_path: Path = DB_PATH) -> None:
    """Schedule a debounced background writeback to CSV and XLSX catalogs."""
    global _pending_timer
    with _debounce_lock:
        if _pending_timer is not None:
            _pending_timer.cancel()
        timer = threading.Timer(SYNC_DEBOUNCE_SEC, _run_debounced_sync, args=(db_path,))
        timer.daemon = True
        _pending_timer = timer
        timer.start()


def stop_catalog_sync() -> None:
    """Cancel any pending debounced catalog sync timer on shutdown."""
    global _pending_timer
    with _debounce_lock:
        if _pending_timer is not None:
            _pending_timer.cancel()
            _pending_timer = None
