"""
Facade for backward compatibility. All implementations have moved to ebook_parser/.
"""

from ebook_parser import (
    safe_xml,
    safe_zip_read,
    clean_spam_text,
    compute_fast_file_hash,
    extract_volume_and_edition,
    normalize_for_dedup,
    parse_bilingual_filename,
    parse_filename_metadata,
    strip_html,
    detect_book_language,
    build_cjk_search_tokens,
    build_cjk_pinyin_search_tokens,
    parse_epub,
    parse_mobi_azw3,
    decompress_palmdoc,
    parse_ebook,
    parse_ebook_with_ocr,
    parse_ebook_full,
)

_decompress_palmdoc = decompress_palmdoc

__all__ = [
    "safe_xml",
    "safe_zip_read",
    "clean_spam_text",
    "compute_fast_file_hash",
    "extract_volume_and_edition",
    "normalize_for_dedup",
    "parse_bilingual_filename",
    "parse_filename_metadata",
    "strip_html",
    "detect_book_language",
    "build_cjk_search_tokens",
    "build_cjk_pinyin_search_tokens",
    "parse_epub",
    "parse_mobi_azw3",
    "decompress_palmdoc",
    "_decompress_palmdoc",
    "parse_ebook",
    "parse_ebook_with_ocr",
    "parse_ebook_full",
]
