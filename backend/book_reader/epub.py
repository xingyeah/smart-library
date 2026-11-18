import logging
import os
import re
import struct
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from parser import safe_xml, safe_zip_read, strip_html
from book_reader.sanitizer import (
    _extract_heading_from_html,
    resolve_zip_path,
)
from book_reader.toc import parse_epub3_nav_html, parse_ncx_navpoints

logger = logging.getLogger(__name__)


def build_epub_structure(
    book_id: int,
    fpath: str,
    cache_get: Any,
    cache_put: Any,
) -> Dict[str, Any]:
    """Parse and cache EPUB OPF spine, manifest, and hierarchical TOC."""
    mtime = os.path.getmtime(fpath)
    cached = cache_get(book_id, mtime)
    if cached is not None:
        return cached

    with zipfile.ZipFile(fpath, "r") as zf:
        namelist = zf.namelist()
        namelist_set = set(namelist)
        lower_map = {n.lower(): n for n in namelist}

        def find_zip_entry(p: str) -> Optional[str]:
            if p in namelist_set:
                return p
            return lower_map.get(p.lower())

        opf_path = None
        if "META-INF/container.xml" in namelist_set:
            try:
                c_xml = safe_xml(safe_zip_read(zf, "META-INF/container.xml"))
                for elem in c_xml.iter():
                    if elem.tag.endswith("rootfile") and elem.attrib.get("full-path"):
                        opf_path = find_zip_entry(elem.attrib["full-path"])
                        break
            except Exception as err:
                logger.debug("Failed container.xml parsing for %s: %s", fpath, err)

        if not opf_path:
            opf_cands = [n for n in namelist if n.lower().endswith(".opf")]
            if opf_cands:
                opf_path = opf_cands[0]

        manifest: Dict[str, Tuple[str, str, str]] = {}
        spine_idrefs: List[str] = []
        opf_dir = ""

        if opf_path:
            opf_dir = str(Path(opf_path).parent)
            if opf_dir == ".":
                opf_dir = ""
            try:
                opf_root = safe_xml(safe_zip_read(zf, opf_path))
                for elem in opf_root.iter():
                    tag = elem.tag.split("}")[-1].lower()
                    if tag == "item":
                        iid = elem.attrib.get("id") or ""
                        href = elem.attrib.get("href") or ""
                        mtype = (elem.attrib.get("media-type") or "").lower()
                        props = (elem.attrib.get("properties") or "").lower()
                        if iid and href:
                            resolved = resolve_zip_path(opf_dir, href)
                            actual = find_zip_entry(resolved) or resolved
                            manifest[iid] = (actual, mtype, props)
                    elif tag == "itemref":
                        idref = elem.attrib.get("idref") or ""
                        if idref:
                            spine_idrefs.append(idref)
            except Exception as err:
                logger.debug("Failed OPF XML read in %s: %s", opf_path, err)

        spine_files: List[str] = []
        seen_files = set()
        for idref in spine_idrefs:
            if idref in manifest:
                f_actual, mtype, _ = manifest[idref]
                if f_actual in namelist_set and f_actual not in seen_files:
                    if "html" in mtype or f_actual.lower().endswith((".xhtml", ".html", ".htm", ".xml")):
                        spine_files.append(f_actual)
                        seen_files.add(f_actual)

        if not spine_files:
            for n in namelist:
                if n.lower().endswith((".xhtml", ".html", ".htm")) and "toc" not in n.lower() and "nav" not in n.lower():
                    spine_files.append(n)
            if not spine_files:
                spine_files = [n for n in namelist if n.lower().endswith((".xhtml", ".html", ".htm"))]

        valid_chapters: List[Dict[str, Any]] = []
        href_to_spine: Dict[str, int] = {}

        for sf in spine_files:
            try:
                raw_bytes = safe_zip_read(zf, sf)
                raw_html = raw_bytes.decode("utf-8", errors="ignore")
            except Exception:
                continue
            plain = strip_html(raw_html).strip()
            has_img = bool(re.search(r"<(?:img|image)\b", raw_html, re.IGNORECASE))
            if len(plain) < 5 and not has_img:
                continue
            c_idx = len(valid_chapters)
            href_to_spine[sf] = c_idx
            heading = _extract_heading_from_html(raw_html)
            valid_chapters.append(
                {
                    "index": c_idx,
                    "file": sf,
                    "dir": str(Path(sf).parent) if str(Path(sf).parent) != "." else "",
                    "title": heading or f"第 {c_idx + 1} 节",
                    "has_custom_title": bool(heading),
                    "chars": len(plain),
                }
            )

        ncx_toc: List[Dict[str, Any]] = []
        nav_toc: List[Dict[str, Any]] = []

        for _, (f_actual, mtype, props) in manifest.items():
            if f_actual not in namelist_set:
                continue
            f_dir = str(Path(f_actual).parent)
            if f_dir == ".":
                f_dir = ""
            if "ncx" in mtype or f_actual.lower().endswith(".ncx"):
                try:
                    ncx_root = safe_xml(safe_zip_read(zf, f_actual))
                    ncx_toc = parse_ncx_navpoints(ncx_root, f_dir, href_to_spine, 0)
                except Exception as err:
                    logger.debug("Failed parsing ncx %s: %s", f_actual, err)
            elif "nav" in props or os.path.basename(f_actual).lower() in ("nav.xhtml", "nav.html", "toc.xhtml"):
                try:
                    nav_raw = safe_zip_read(zf, f_actual).decode("utf-8", errors="ignore")
                    nav_toc = parse_epub3_nav_html(nav_raw, f_dir, href_to_spine)
                except Exception as err:
                    logger.debug("Failed parsing nav %s: %s", f_actual, err)

        if not ncx_toc:
            for n in namelist:
                if n.lower().endswith(".ncx"):
                    try:
                        n_dir = str(Path(n).parent)
                        if n_dir == ".":
                            n_dir = ""
                        ncx_root = safe_xml(safe_zip_read(zf, n))
                        ncx_toc = parse_ncx_navpoints(ncx_root, n_dir, href_to_spine, 0)
                        if ncx_toc:
                            break
                    except Exception as err:
                        logger.debug("Failed scanning fallback ncx %s: %s", n, err)

        toc_list = nav_toc if len(nav_toc) > len(ncx_toc) else ncx_toc

        spine_first_toc_title: Dict[int, str] = {}
        for t_item in toc_list:
            s_idx = t_item["chapter_idx"]
            if s_idx not in spine_first_toc_title and t_item["title"]:
                spine_first_toc_title[s_idx] = t_item["title"]

        for ch in valid_chapters:
            if ch["index"] in spine_first_toc_title:
                ch["title"] = spine_first_toc_title[ch["index"]]

        if not toc_list:
            toc_list = [
                {
                    "title": ch["title"],
                    "chapter_idx": ch["index"],
                    "anchor": "",
                    "level": 0,
                }
                for ch in valid_chapters
            ]

        for idx, t in enumerate(toc_list):
            t["toc_id"] = idx

        struct_data = {
            "mode": "epub",
            "chapters": valid_chapters,
            "toc": toc_list,
            "href_to_spine": href_to_spine,
        }
        cache_put(book_id, mtime, struct_data)
        return struct_data
