"""
Blocks of a PDF page for side-by-side translation: headings, list items and paragraphs, in reading order, each
with where it starts on the page. Works on the lines PDFKit reports (text plus box in points from the top-left);
a line's box height stands in for its font size.

Rules, tuned on book pages: running headers and footers are dropped; two-column text is read column by column
within each section, and a heading starts a new section; a block ends at a size change, a vertical gap, a list
marker, or a short line that closes a sentence (first-line indents are not used: list items and epigraphs
are indented too).
"""

import re
from statistics import median
from typing import Any, Dict, List

RUNNING_MARGIN = 0.06  # running headers and footers sit in the top or bottom 6% of the page
RUNNING_MAX_CHARS = 80
SIZE_STEP = 1.08  # line heights differing by more than this are different fonts
HEADING_MAX_CHARS = 120
HEADING_MAX_LINES = 2  # body text set larger than the rest of the page is still body text
TITLE_SIZE = 1.5  # a heading this many times the body size is a top-level title
SECTION_BREAK_SIZE = 1.3  # a heading this large interrupts the columns (both columns continue below it)
PARAGRAPH_GAP = 0.5  # vertical gap, in line heights, that separates two blocks
FRAGMENT_MAX_WIDTH = 3.0  # a list marker or bullet set apart on its line is at most 3 line heights wide
FRAGMENT_MAX_GAP = 1.5
TWO_COLUMN_SHARE = 0.3  # share of lines that must start in the right half for a page to have two columns
COLUMN_MARGIN = 5.0  # points a full-width line must reach past the page middle on both sides
SHORT_LINE = 0.85  # a line shorter than this share of the block's widest line can end a paragraph
_SENTENCE_END = (".", "!", "?", ":", "。", "！", "？", "：", '"', "”")
_LIST_MARKER = re.compile(r"^(\d{1,3}[.)]|\(\d{1,3}\)|[a-zA-Z][.)]|[•·▪◦●■*–—-])\s")
_PAGE_NUMBER = re.compile(r"^[\divxlcIVXLC]{1,6}$")
_CJK_EDGE = re.compile(r"[一-鿿，。、；：！？）」』]$")


def _body_height(lines: List[Dict[str, Any]]) -> float:
    """Most common text size, weighted by characters, so a few big headings do not count."""
    sizes = [line["h"] for line in lines for _ in range(min(len(line["t"]), 200))]
    return median(sizes) if sizes else 10.0


def _merge_fragments(lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """'1.' and '1.1 Electrical …' come as two lines on one baseline; join a short fragment to its right neighbour."""
    merged: List[Dict[str, Any]] = []
    for line in sorted(lines, key=lambda item: (round(item["y"]), item["x"])):
        prev = merged[-1] if merged else None
        gap = line["x"] - (prev["x"] + prev["w"]) if prev else 0.0
        same_row = prev is not None and abs(prev["y"] - line["y"]) < prev["h"] * 0.3
        if same_row and prev["w"] < FRAGMENT_MAX_WIDTH * prev["h"] and 0 <= gap < FRAGMENT_MAX_GAP * prev["h"]:
            prev.update(t=f"{prev['t']} {line['t']}", w=line["x"] + line["w"] - prev["x"], h=max(prev["h"], line["h"]))
        else:
            merged.append(dict(line))
    return merged


def _is_running_line(line: Dict[str, Any], page_height: float, body_h: float) -> bool:
    in_margin = line["y"] < page_height * RUNNING_MARGIN or line["y"] + line["h"] > page_height * (1 - RUNNING_MARGIN)
    small = line["h"] <= body_h * 1.05 and len(line["t"]) <= RUNNING_MAX_CHARS
    return in_margin and (small or bool(_PAGE_NUMBER.match(line["t"])))


def _reading_order(lines: List[Dict[str, Any]], page_width: float, body_h: float) -> List[Dict[str, Any]]:
    """Lines top to bottom, the left column before the right one inside each section; tags each line with its
    column. A section ends at a large heading, or where full-width text follows the columns."""
    middle = page_width / 2
    in_right_half = [line["x"] >= middle - COLUMN_MARGIN for line in lines]
    two_columns = sum(in_right_half) >= TWO_COLUMN_SHARE * max(1, len(lines))  # not a right-aligned epigraph
    section, has_right_column, prev_big, ordered = 0, False, False, []
    for line in sorted(lines, key=lambda item: item["y"]):
        column = 1 if two_columns and line["x"] >= middle - COLUMN_MARGIN else 0
        full_width = line["x"] < middle - COLUMN_MARGIN < middle + COLUMN_MARGIN < line["x"] + line["w"]
        big = line["h"] >= body_h * SECTION_BREAK_SIZE
        if (big and not prev_big) or (full_width and has_right_column):
            section, has_right_column = section + 1, False
        prev_big = big
        has_right_column = has_right_column or column == 1
        ordered.append(dict(line, section=section, column=column))
    return sorted(ordered, key=lambda item: (item["section"], item["column"], item["y"]))


def _starts_block(prev: Dict[str, Any], line: Dict[str, Any], widest: float) -> bool:
    if (prev["section"], prev["column"]) != (line["section"], line["column"]):
        return True
    if max(prev["h"], line["h"]) > min(prev["h"], line["h"]) * SIZE_STEP:
        return True
    if line["y"] - (prev["y"] + prev["h"]) > PARAGRAPH_GAP * prev["h"] or _LIST_MARKER.match(line["t"]):
        return True
    return prev["t"].endswith(_SENTENCE_END) and prev["w"] < widest * SHORT_LINE


def _join(texts: List[str]) -> str:
    out = ""
    for text in texts:
        if out.endswith("-") and text[:1].islower():
            out = out[:-1] + text  # word broken across lines
        elif out and not (_CJK_EDGE.search(out) or re.match(r"[一-鿿]", text)):
            out = f"{out} {text}"
        else:
            out += text
    return out


def _role(block_lines: List[Dict[str, Any]], text: str, body_h: float) -> str:
    size = block_lines[0]["h"]
    short = len(block_lines) <= HEADING_MAX_LINES and len(text) <= HEADING_MAX_CHARS
    if short and size >= body_h * TITLE_SIZE:
        return "title"
    if short and size >= body_h * SIZE_STEP:
        return "heading"
    return "list" if _LIST_MARKER.match(text) else "para"


def page_blocks(lines: List[Dict[str, Any]], page_width: float, page_height: float) -> List[Dict[str, Any]]:
    """[{"text", "role" (title|heading|list|para), "top" (share of the page height)}] in reading order."""
    lines = [line for line in lines if line["t"].strip()]
    body_h = _body_height(lines)
    lines = [line for line in _merge_fragments(lines) if not _is_running_line(line, page_height, body_h)]
    groups: List[List[Dict[str, Any]]] = []
    for line in _reading_order(lines, page_width, body_h):
        widest = max((item["w"] for item in groups[-1]), default=0.0) if groups else 0.0
        if groups and not _starts_block(groups[-1][-1], line, widest):
            groups[-1].append(line)
        else:
            groups.append([line])
    blocks = []
    for group in groups:
        text = _join([line["t"] for line in group])
        if re.search(r"[A-Za-z一-鿿]", text):
            blocks.append({"text": text, "role": _role(group, text, body_h),
                           "top": round(min(line["y"] for line in group) / page_height, 4)})
    return blocks
