"""Unified SQL WHERE predicate builders for book shelf, hybrid retrieval, and interest picks."""

from datetime import date
from typing import Any, List, Optional, Tuple

from search.types import ShelfFilters

_LANG_LOOKUP = {
    "zh-cn": ["zh-CN", "zh-cn", "zh"],
    "cn": ["zh-CN", "zh-cn", "zh"],
    "简体": ["zh-CN", "zh-cn", "zh"],
    "简体中文": ["zh-CN", "zh-cn", "zh"],
    "zh-tw": ["zh-TW", "zh-tw"],
    "tw": ["zh-TW", "zh-tw"],
    "繁体": ["zh-TW", "zh-tw"],
    "繁体中文": ["zh-TW", "zh-tw"],
    "zh": ["zh-CN", "zh-cn", "zh-TW", "zh-tw", "zh"],
    "chinese": ["zh-CN", "zh-cn", "zh-TW", "zh-tw", "zh"],
    "中文": ["zh-CN", "zh-cn", "zh-TW", "zh-tw", "zh"],
    "en": ["en", "EN", "en-US", "en-GB"],
    "english": ["en", "EN", "en-US", "en-GB"],
    "英文": ["en", "EN", "en-US", "en-GB"],
}


def parse_language_filter(language: str) -> List[str]:
    """Parse comma-separated language values into whitelist list with case-safe variants."""
    if not language or language in ("all", "全部"):
        return []
    parts = [p.strip().lower() for p in language.split(",") if p.strip()]
    res: List[str] = []
    for p in parts:
        matched = _LANG_LOOKUP.get(p)
        if matched:
            res.extend(matched)
        else:
            res.extend([p, p.upper(), p.lower()])
    return list(dict.fromkeys(res))


def build_language_clause(lang_list: List[str], table_alias: str = "") -> Tuple[str, List[str]]:
    """Return a parameterized COLLATE NOCASE IN predicate for parsed language values."""
    if not lang_list:
        return "", []
    col = f"{table_alias}.language" if table_alias else "language"
    placeholders = ",".join("?" for _ in lang_list)
    return f"{col} COLLATE NOCASE IN ({placeholders})", list(lang_list)


def build_pub_period_clause(period: str, table_alias: str = "b") -> str:
    """Map publication year era filter to SQL condition."""
    if not period or period == "全部":
        return ""
    col = f"{table_alias}.pub_year" if table_alias else "pub_year"
    glob_patterns = {
        "2020s": "202[0-9]*",
        "2010s": "201[0-9]*",
        "2000s": "200[0-9]*",
        "1990s": "199[0-9]*",
        "1900s": "19[0-9][0-9]*",
    }
    if period == "thisyear":
        return f"{col} GLOB '{date.today().year}*'"
    if period in glob_patterns:
        return f"{col} GLOB '{glob_patterns[period]}'"
    if period == "pre1900":
        return f"{col} != '' AND {col} GLOB '[0-9]*' AND CAST({col} AS INT) < 1900"
    if period == "ancient":
        return (
            f"{col} != '' AND ({col} GLOB '19[0-8][0-9]*' "
            f"OR {col} GLOB '1[0-8][0-9][0-9]*' OR CAST({col} AS INT) < 1990)"
        )
    return ""


def build_book_facet_where(
    filters: ShelfFilters,
    table_alias: str = "",
    lang_list: Optional[List[str]] = None,
) -> Tuple[List[str], List[Any]]:
    """Build WHERE predicates and bind params for common book catalog facets."""
    prefix = f"{table_alias}." if table_alias else ""
    id_ref = f"{prefix}id" if prefix else "books.id"
    clauses = [f"{prefix}is_deleted = 0"]
    params: List[Any] = []

    for col, val in (
        ("category", filters.category),
        ("primary_format", filters.fmt.lower() if filters.fmt else ""),
        ("source_dir", filters.source_dir),
        ("reading_status", filters.reading_status),
    ):
        if val and val != "全部":
            clauses.append(f"{prefix}{col} = ?")
            params.append(val)

    resolved_langs = parse_language_filter(filters.language) if lang_list is None else lang_list
    lang_clause, lang_params = build_language_clause(resolved_langs, table_alias=table_alias)
    if lang_clause:
        clauses.append(lang_clause)
        params.extend(lang_params)
    if filters.only_favorite:
        clauses.append(f"{prefix}is_favorite = 1")
    if filters.has_rating:
        clauses.append(
            f"({prefix}douban_rating > 0 OR EXISTS ("
            f"SELECT 1 FROM book_external_meta em "
            f"WHERE em.book_id = {id_ref} AND (em.rating > 0 OR (em.reviews_json IS NOT NULL AND em.reviews_json != '[]'))"
            f"))"
        )
    period_clause = build_pub_period_clause(filters.pub_period, table_alias=table_alias)
    if period_clause:
        clauses.append(period_clause)
    return clauses, params
