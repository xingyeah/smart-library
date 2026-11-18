"""
Facade for backward compatibility. All implementations have moved to book_reader/.
"""

from book_reader.sanitizer import (
    _js_string,
    resolve_zip_path,
    sanitize_and_format_book_html,
)
from book_reader.toc import parse_epub3_nav_html, parse_ncx_navpoints
from book_reader.epub import build_epub_structure
from book_reader.mobi import (
    HuffCdicDecompressor,
    build_mobi_azw3_structure,
)
from book_reader.text import build_txt_structure
from book_reader.payload import (
    _chapter_list,
    extract_book_reader_content,
    get_epub_asset_bytes,
    get_mobi_image_bytes,
)

# Aliases for internal underscores if tests patched them
_build_epub_structure = build_epub_structure
_build_mobi_azw3_structure = build_mobi_azw3_structure
_build_txt_structure = build_txt_structure
_HuffCdicDecompressor = HuffCdicDecompressor
_parse_ncx_navpoints = parse_ncx_navpoints
_parse_epub3_nav_html = parse_epub3_nav_html

__all__ = [
    "resolve_zip_path",
    "sanitize_and_format_book_html",
    "parse_ncx_navpoints",
    "parse_epub3_nav_html",
    "build_epub_structure",
    "HuffCdicDecompressor",
    "build_mobi_azw3_structure",
    "build_txt_structure",
    "extract_book_reader_content",
    "get_epub_asset_bytes",
    "get_mobi_image_bytes",
    "_chapter_list",
    "_js_string",
    "_build_epub_structure",
    "_build_mobi_azw3_structure",
    "_build_txt_structure",
    "_HuffCdicDecompressor",
    "_parse_ncx_navpoints",
    "_parse_epub3_nav_html",
]
