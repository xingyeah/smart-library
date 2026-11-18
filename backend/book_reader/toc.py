import os
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional
from parser import strip_html
from book_reader.sanitizer import resolve_zip_path


def parse_ncx_navpoints(
    parent_elem: ET.Element,
    ncx_dir: str,
    href_to_spine: Dict[str, int],
    level: int = 0,
) -> List[Dict[str, Any]]:
    """Recursively parse EPUB2 NCX <navPoint> tree into hierarchical TOC items."""
    items: List[Dict[str, Any]] = []
    for child in list(parent_elem):
        tag = child.tag.split("}")[-1].lower()
        if tag == "navpoint":
            label_txt = ""
            content_src = ""
            for sub in list(child):
                stag = sub.tag.split("}")[-1].lower()
                if stag == "navlabel":
                    for t_el in sub.iter():
                        if t_el.tag.split("}")[-1].lower() == "text" and t_el.text:
                            label_txt = " ".join(t_el.text.split()).strip()
                            break
                elif stag == "content":
                    content_src = sub.attrib.get("src") or ""

            if label_txt and content_src:
                parts = content_src.split("#", 1)
                rel_path = resolve_zip_path(ncx_dir, parts[0])
                anchor = parts[1].strip() if len(parts) > 1 else ""
                spine_idx = href_to_spine.get(rel_path, -1)
                if spine_idx == -1 and rel_path:
                    rel_lower = rel_path.lower()
                    for k, v in href_to_spine.items():
                        if k.lower() == rel_lower or k.lower().endswith("/" + os.path.basename(rel_lower)):
                            spine_idx = v
                            break
                if spine_idx >= 0:
                    items.append(
                        {
                            "title": label_txt,
                            "chapter_idx": spine_idx,
                            "anchor": anchor,
                            "level": min(level, 3),
                        }
                    )
            sub_items = parse_ncx_navpoints(child, ncx_dir, href_to_spine, level + 1)
            items.extend(sub_items)
        elif tag in ("navmap", "ncx"):
            items.extend(parse_ncx_navpoints(child, ncx_dir, href_to_spine, level))
    return items


def _match_spine_index(href_to_spine: Dict[str, int], rel_path: str) -> int:
    """Resolve zip relative path to spine item index, with case-insensitive fallback."""
    spine_idx = href_to_spine.get(rel_path, -1)
    if spine_idx != -1 or not rel_path:
        return spine_idx
    rel_lower = rel_path.lower()
    suffix = "/" + os.path.basename(rel_lower)
    for k, v in href_to_spine.items():
        if k.lower() == rel_lower or k.lower().endswith(suffix):
            return v
    return -1


def _parse_nav_link_token(
    attrs: str, inner: str, nav_dir: str, href_to_spine: Dict[str, int], level: int
) -> Optional[Dict[str, Any]]:
    """Parse a single <a> link token within EPUB3 nav toc into a structured toc dict."""
    lbl = " ".join(strip_html(inner).split()).strip()
    href_m = re.search(r'href=["\']([^"\']+)["\']', attrs, re.IGNORECASE)
    if not (lbl and href_m):
        return None
    raw_href = href_m.group(1).strip()
    parts = raw_href.split("#", 1)
    rel_path = resolve_zip_path(nav_dir, parts[0]) if parts[0] else ""
    anchor = parts[1].strip() if len(parts) > 1 else ""
    spine_idx = _match_spine_index(href_to_spine, rel_path)
    if spine_idx < 0:
        return None
    return {
        "title": lbl,
        "chapter_idx": spine_idx,
        "anchor": anchor,
        "level": max(0, min(level, 3)),
    }


def parse_epub3_nav_html(
    nav_html: str,
    nav_dir: str,
    href_to_spine: Dict[str, int],
) -> List[Dict[str, Any]]:
    """Parse EPUB3 <nav epub:type="toc"> nested <ol>/<li> structure into hierarchical TOC items."""
    items: List[Dict[str, Any]] = []
    nav_m = re.search(
        r'<nav[^>]*?(?:epub:type|role)=["\'][^"\']*toc[^"\']*["\'][^>]*>(.*?)</nav>',
        nav_html,
        re.DOTALL | re.IGNORECASE,
    )
    scope = nav_m.group(1) if nav_m else nav_html
    level = -1
    token_re = re.compile(
        r"(<\s*ol\b[^>]*>|<\s*/\s*ol\s*>|<\s*a\b([^>]*)>(.*?)<\s*/\s*a\s*>)",
        re.DOTALL | re.IGNORECASE,
    )

    for m in token_re.finditer(scope):
        full_tok = m.group(1).lower().strip()
        if full_tok.startswith("<ol"):
            level += 1
            continue
        if full_tok.startswith("</ol"):
            level = max(-1, level - 1)
            continue
        item = _parse_nav_link_token(m.group(2) or "", m.group(3) or "", nav_dir, href_to_spine, level)
        if item:
            items.append(item)
    return items
