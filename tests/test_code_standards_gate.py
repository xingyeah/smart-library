"""
Code Standards & Architectural Decoupling AST Quality Gate.

Enforces:
1. File length: Every .py file under backend/ must not exceed 500 lines.
2. Silent exceptions: No unannotated 'except ...: pass' constructs in backend/.
3. Function length and cyclomatic depth: Functions <= 50 lines and nesting depth <= 3
   (exemptions strictly tracked in ALLOWED_FUNCTION_EXEMPTIONS with documented rationale; list can only shrink).
4. Parameter counts: Public APIs have <= 3 parameters (or use parameter objects; legacy facades tracked).
"""

import ast
import os
from pathlib import Path
import tokenize
import unittest
from typing import Dict, List, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"

# Rationale documented for every exempted function. This whitelist can only shrink.
ALLOWED_FUNCTION_EXEMPTIONS: Dict[Tuple[str, str], str] = {
    ("database.py", "init_db"): "Multi-table SQLite DDL schema, indices, and virtual tables definition",
    ("book_reader/epub.py", "build_epub_structure"): "EPUB zip archive navigation, OPF spine parsing, and chapter extraction",
    ("book_reader/mobi.py", "_get_mobi_trailing_size"): "PalmDOC trailing byte flags bitmask decoding",
    ("book_reader/mobi.py", "build_mobi_azw3_structure"): "PalmDOC record iteration, HUFF/CDIC decompression, and MOBI section headers",
    ("book_reader/payload.py", "extract_book_reader_content"): "Format dispatcher and reading payload assembler for EPUB/MOBI/AZW3/TXT/PDF",
    ("book_reader/payload.py", "get_epub_asset_bytes"): "EPUB internal asset extractor with zip path resolution",
    ("book_reader/sanitizer.py", "_extract_heading_from_html"): "Regex extraction of h1-h6 headings from HTML fragments",
    ("book_reader/sanitizer.py", "sanitize_and_format_book_html"): "HTML parser, tag whitelisting, image src URL rewriting, and typography normalization",
    ("book_reader/toc.py", "parse_ncx_navpoints"): "Recursive NCX hierarchical XML navPoint parsing",
    ("ebook_parser/epub.py", "parse_epub"): "EPUB OPF Dublin Core XML metadata extraction, cover image discovery, and TOC",
    ("ebook_parser/filenames.py", "parse_bilingual_filename"): "Complex bilingual title/author regex matching rules",
    ("ebook_parser/filenames.py", "parse_filename_metadata"): "Filename sanitization, volume parsing, series, and edition tagging",
    ("ebook_parser/language.py", "detect_book_language"): "Unicode character frequency, CJK block analysis, and script detection",
    ("ebook_parser/mobi.py", "decompress_palmdoc"): "PalmDOC compression RLE / copy-offset byte decoder loop",
    ("ebook_parser/mobi.py", "parse_mobi_azw3"): "Palm database header parser, EXTH record tags, and PalmDOC decoding",
    ("ebook_parser/pipeline.py", "_parse_pipeline"): "Unified parse orchestration across EPUB, MOBI, PDF, TXT, OCR fallback",
    ("enricher.py", "synthesize_structured_synopsis"): "Rule-based synopsis generation and tag clustering",
    ("search/intent.py", "analyze_query_intent"): "Natural language query tokenization, domain vocabulary matching, category inference",
    ("search/intent.py", "infer_query_categories"): "Multi-category keyword matcher with confidence thresholds",
    ("search/recall.py", "execute_dual_path_recall"): "Parallel FTS5, LIKE, and BGE vector candidate recall",
    ("search/scoring.py", "pre_score_candidates"): "BM25 scoring, exact title matching, classic score weighting, and decay curves",
    ("search/shelf.py", "apply_shelf_sort"): "In-memory multi-attribute comparator sorting",
    ("search/shelf.py", "search_empty_shelf"): "Empty-query facet aggregation, pagination, and multi-filter SQL queries",
    ("search/finalize.py", "rerank_and_fuse_candidates"): "RRF rank fusion and BGE-Reranker scoring",
    ("search/corpus_stats.py", "ensure_author_lexicon"): "Inverted index author cache builder",
    ("search/retrieval.py", "_retrieve_one_language"): "Multi-stage bilingual retrieval coordinator",
    ("classic_scorer.py", "compute_book_classic_Signals"): "Classic literature heuristic scoring signals",
    ("classic_scorer.py", "backfill_languages_and_classic_scores"): "Batch DB migration for classic scores and language tags",
    ("build_bge_index.py", "_sync_bge_m3_index"): "1024-dim dense vector embedding pipeline with batch pooling",
    ("archiver.py", "discover_source_files"): "Nested directory traversal across multiple book source directories",
    ("archiver.py", "rollback_organize_run"): "Hardlink undo with multi-link safety checks",
    ("archiver.py", "run_organize_pipeline"): "Multi-threaded import pipeline coordinator",
    ("archiver.py", "_flush_batch"): "Batched database persistence with exception logging",
    ("http_api/handlers/books.py", "handle_get_stats"): "Multi-table aggregation for library status dashboard",
    ("http_api/handlers/books.py", "handle_post_book_meta"): "Book metadata update handler with JSON body validation",
    ("cli.py", "main"): "CLI argument parsing and command dispatch",
    ("enrich_ai.py", "cmd_run"): "Batch AI enrichment CLI with multi-threading",
    ("optimize_summaries.py", "load_xlsx_records"): "Excel file parser with cell type validation",
    ("optimize_summaries.py", "run_summary_optimization"): "Batch book synopsis optimization workflow",
    ("repair_titles.py", "cmd_translate"): "Title translation CLI runner",
    ("standardize_titles.py", "extract_title_from_filenames"): "Batch title regex extraction across legacy files",
    ("standardize_titles.py", "batch_translate_titles"): "Batch title translation loop",
    ("standardize_titles.py", "run_standardization"): "Book library title normalization script",
    ("translator.py", "get_cached_translation"): "Cached translation lookup with JSON deserialization",
    ("translator.py", "_translate_via_google"): "Google translate web scraper fallback",
    ("translator.py", "batch_translate_paragraphs"): "Multi-paragraph translation splitter and merger",
    ("eval_build.py", "cmd_label"): "Evaluation dataset labeling CLI loop",
    ("eval_search.py", "print_report"): "Evaluation nDCG score formatted table printer",
}

# Whitelist of public APIs with > 3 parameters (legacy facade signatures or external protocol mappers)
ALLOWED_PARAM_EXEMPTIONS: Dict[Tuple[str, str], str] = {
    ("archiver.py", "safe_hardlink_to_library"): "Legacy 6-arg signature retained for compatibility; ArchiveTarget supported",
    ("archiver.py", "upsert_enriched_book"): "Core book import function taking conn, item, run_id, do_archive_link",
    ("archiver.py", "run_organize_pipeline"): "Batch pipeline orchestration entrypoint with 5 optional config parameters",
    ("search/__init__.py", "instant_search"): "Legacy 11-arg facade retained for compatibility; ShelfQuery supported",
    ("search/shelf.py", "search_empty_shelf"): "Internal package empty shelf query builder",
    ("search/finalize.py", "rerank_and_fuse_candidates"): "Internal package rerank fusion step",
    ("search/recall.py", "execute_dual_path_recall"): "Internal package dual-path recall step",
    ("search/scoring.py", "pre_score_candidates"): "Internal package scoring engine step",
    ("search/recommend.py", "recommend_by_description"): "Public recommendation API with query, top_k, language, cross_encoder",
    ("book_reader/epub.py", "build_epub_structure"): "Reader module interface: book_id, fpath, cache_get, cache_put",
    ("book_reader/mobi.py", "build_mobi_azw3_structure"): "Reader module interface: book_id, fpath, cache_get, cache_put",
    ("book_reader/sanitizer.py", "sanitize_and_format_book_html"): "Legacy HTML sanitizer interface with formatting flags",
    ("book_reader/text.py", "build_txt_structure"): "Reader module interface: book_id, fpath, cache_get, cache_put",
    ("book_reader/toc.py", "parse_ncx_navpoints"): "NCX recursive navpoint parsing interface",
    ("ebook_parser/language.py", "detect_book_language"): "Multi-feature language detector taking content, metadata, and hints",
    ("enricher.py", "classify_book_category"): "Heuristic classification taking title, author, summary, tags, path, series",
    ("deepseek_client.py", "chat"): "1:1 direct mapping of external LLM API parameter options",
    ("classic_scorer.py", "compute_book_classic_Signals"): "Multi-attribute classical scoring signals evaluator",
    ("semantic_models.py", "dense_search_by_vector"): "Vector search query vector, allowed_ids, top_k, min_sim",
    ("semantic_models.py", "rerank_pairs"): "Cross-encoder batch reranking query, pairs, max_length, batch_size",
    ("ai_search.py", "rerank"): "AI semantic reranker interface",
    ("ai_search.py", "pick_books"): "AI recommendation book selector",
    ("enrich_ai.py", "cmd_run"): "Batch enrichment CLI command options",
    ("translator.py", "save_cached_translation"): "Multi-column translation cache write interface",
    ("translator.py", "translate_text"): "Translation options: text, from_lang, to_lang, max_retries, timeout",
    ("translator.py", "batch_translate_paragraphs"): "Batch translation options",
    ("http_api/handlers/books.py", "handle_get_book_detail"): "Standard HTTP handler signature (handler, params, body, query)",
    ("http_api/handlers/books.py", "handle_post_book_progress"): "Standard HTTP handler signature",
    ("http_api/handlers/books.py", "handle_post_book_meta"): "Standard HTTP handler signature",
    ("http_api/handlers/books.py", "handle_post_book_enrich_refresh"): "Standard HTTP handler signature",
    ("http_api/handlers/books.py", "handle_post_book_delete"): "Standard HTTP handler signature",
    ("http_api/handlers/organize.py", "handle_post_conflict_resolve"): "Standard HTTP handler signature",
    ("http_api/handlers/reading.py", "handle_get_reader_content"): "Standard HTTP handler signature",
    ("http_api/handlers/reading.py", "handle_get_epub_asset"): "Standard HTTP handler signature",
    ("http_api/handlers/pdf_pages.py", "handle_get_pdf_layout"): "Standard HTTP handler signature",
    ("http_api/handlers/pdf_pages.py", "handle_get_pdf_page"): "Standard HTTP handler signature",
    ("http_api/handlers/pdf_pages.py", "handle_get_pdf_text"): "Standard HTTP handler signature",
    ("native_bridge.py", "render_pdf_page"): "1:1 mapping of the mac_pdf_render_page C entrypoint",
    ("http_api/handlers/reading.py", "handle_get_mobi_image"): "Standard HTTP handler signature",
    ("http_api/handlers/reading.py", "handle_get_book_file"): "Standard HTTP handler signature with range header",
    ("http_api/handlers/reading.py", "handle_get_cover"): "Standard HTTP handler signature",
    ("http_api/handlers/reading.py", "handle_get_vendor"): "Standard HTTP handler signature",
    ("http_api/responses.py", "send_range_stream"): "Range response streaming helper with status, range, headers",
    ("http_api/router.py", "dispatch"): "HTTP request router dispatcher",
}


def _get_nesting_depth(node: ast.AST, current_depth: int = 0) -> int:
    """Calculate maximum block nesting depth inside an AST node."""
    max_d = current_depth
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.If, ast.For, ast.While, ast.With, ast.Try)):
            d = _get_nesting_depth(child, current_depth + 1)
            if d > max_d:
                max_d = d
        else:
            d = _get_nesting_depth(child, current_depth)
            if d > max_d:
                max_d = d
    return max_d


class CodeStandardsGateTests(unittest.TestCase):
    """AST automated enforcement gate for codebase quality standards."""

    def test_file_line_counts_do_not_exceed_500(self):
        """Every Python source file in backend/ must be <= 500 lines."""
        violations = []
        for pyfile in sorted(BACKEND_DIR.rglob("*.py")):
            rel = pyfile.relative_to(REPO_ROOT)
            with open(pyfile, "r", encoding="utf-8") as f:
                count = len(f.readlines())
            if count > 500:
                violations.append(f"{rel}: {count} lines (> 500)")

        self.assertEqual(
            violations,
            [],
            "The following files exceed the 500-line modularity limit:\n" + "\n".join(violations),
        )

    def test_no_uncommented_bare_except_pass(self):
        """'except ...: pass' is allowed only for a client that hung up; a comment alone does not excuse the rest."""
        violations = []
        for pyfile in sorted(BACKEND_DIR.rglob("*.py")):
            rel = str(pyfile.relative_to(REPO_ROOT))
            with open(pyfile, "r", encoding="utf-8") as f:
                code = f.read()

            try:
                tree = ast.parse(code, filename=str(pyfile))
            except SyntaxError as e:
                self.fail(f"Syntax error parsing {rel}: {e}")

            with open(pyfile, "rb") as f:
                tokens = list(tokenize.tokenize(f.readline))

            comment_lines = {tok.start[0] for tok in tokens if tok.type == tokenize.COMMENT}

            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler):
                    if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                        pass_line = node.body[0].lineno
                        # Must have a comment on except line, pass line, or adjacent
                        has_comment = any(
                            line in comment_lines
                            for line in range(node.lineno, pass_line + 2)
                        )
                        if not has_comment:
                            violations.append(f"{rel}:{node.lineno} uncommented except...pass")
                        elif rel != "backend/http_api/server_handler.py":
                            # Writing the error response to a closed socket is the one place with nothing left to do
                            violations.append(f"{rel}:{node.lineno} silent except: log it or handle it")

        self.assertEqual(
            violations,
            [],
            "The following silent exceptions lack explanatory comments:\n" + "\n".join(violations),
        )

    def test_function_length_and_nesting_depth(self):
        """Functions must be <= 50 lines and depth <= 3 unless explicitly documented in ALLOWED_FUNCTION_EXEMPTIONS."""
        violations = []
        found_exemptions: Set[Tuple[str, str]] = set()

        for pyfile in sorted(BACKEND_DIR.rglob("*.py")):
            rel_mod = str(pyfile.relative_to(BACKEND_DIR))
            with open(pyfile, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=str(pyfile))

            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    lines = node.end_lineno - node.lineno + 1 if hasattr(node, "end_lineno") else 0
                    depth = _get_nesting_depth(node, 0)
                    key = (rel_mod, node.name)

                    if lines > 50 or depth > 3:
                        if key in ALLOWED_FUNCTION_EXEMPTIONS:
                            found_exemptions.add(key)
                        else:
                            violations.append(
                                f"{rel_mod}:{node.lineno} {node.name}() lines={lines}, depth={depth}"
                            )

        # The list may only shrink: an exemption for a function that is now compliant (or gone) must be deleted
        stale_exemptions = set(ALLOWED_FUNCTION_EXEMPTIONS.keys()) - found_exemptions
        self.assertEqual(sorted(stale_exemptions), [], "Exemptions no longer needed; delete them from ALLOWED_FUNCTION_EXEMPTIONS")

        self.assertEqual(
            violations,
            [],
            "Functions exceeding 50 lines or 3 nesting levels without exemption:\n"
            + "\n".join(violations),
        )

    def test_public_api_parameter_counts(self):
        """Public functions must accept <= 3 parameters unless listed in ALLOWED_PARAM_EXEMPTIONS."""
        violations = []
        for pyfile in sorted(BACKEND_DIR.rglob("*.py")):
            rel_mod = str(pyfile.relative_to(BACKEND_DIR))
            with open(pyfile, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=str(pyfile))

            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name.startswith("_"):
                        continue
                    args_count = len(node.args.args)
                    if node.args.vararg:
                        args_count += 1
                    if node.args.kwarg:
                        args_count += 1
                    args_count += len(node.args.kwonlyargs)
                    if node.args.args and node.args.args[0].arg in ("self", "cls"):
                        args_count -= 1

                    key = (rel_mod, node.name)
                    if args_count > 3:
                        if key not in ALLOWED_PARAM_EXEMPTIONS:
                            violations.append(
                                f"{rel_mod}:{node.lineno} {node.name}() has {args_count} arguments (> 3)"
                            )

        self.assertEqual(
            violations,
            [],
            "Public functions with > 3 parameters not tracked in parameter exemptions:\n"
            + "\n".join(violations),
        )


if __name__ == "__main__":
    unittest.main()
