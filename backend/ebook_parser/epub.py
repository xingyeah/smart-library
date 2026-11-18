import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple
import zipfile
from ebook_parser.safe_io import safe_xml, safe_zip_read
from ebook_parser.filenames import strip_html

logger = logging.getLogger(__name__)


def parse_epub(file_path: str, cover_out_path: str = "") -> Dict[str, Any]:
    """Fast native ZIP/XML parser for EPUB metadata, cover image, NCX/Nav TOC, and opening chapters."""
    res: Dict[str, Any] = {
        "title": "",
        "subtitle": "",
        "author": "",
        "translator": "",
        "publisher": "",
        "pub_year": "",
        "isbn": "",
        "douban_id": "",
        "series": "",
        "tags": [],
        "description": "",
        "toc": [],
        "text_sample": "",
        "cover_saved": False,
    }
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            namelist = zf.namelist()
            opf_path = None
            if "META-INF/container.xml" in namelist:
                try:
                    c_xml = safe_xml(safe_zip_read(zf, "META-INF/container.xml"))
                    for elem in c_xml.iter():
                        if elem.tag.endswith("rootfile") and elem.attrib.get("full-path"):
                            opf_path = elem.attrib["full-path"]
                            break
                except Exception as err:
                    logger.debug("Failed reading container.xml in %s: %s", file_path, err)
            if not opf_path:
                opf_candidates = [n for n in namelist if n.lower().endswith(".opf")]
                if opf_candidates:
                    opf_path = opf_candidates[0]

            if not opf_path or opf_path not in namelist:
                return res

            opf_dir = str(Path(opf_path).parent)
            if opf_dir == ".":
                opf_dir = ""

            def resolve_href(href: str) -> str:
                href = href.split("#")[0]
                if not opf_dir:
                    return href
                return f"{opf_dir}/{href}".lstrip("/")

            opf_root = safe_xml(safe_zip_read(zf, opf_path))
            authors = []
            tags = []
            cover_id = None
            manifest: Dict[str, Tuple[str, str, str]] = {}
            spine_ids: List[str] = []

            for elem in opf_root.iter():
                tag = elem.tag.split("}")[-1].lower()
                text = (elem.text or "").strip()
                if tag == "title" and text and not res["title"]:
                    res["title"] = text
                elif tag == "creator" and text:
                    authors.append(text)
                elif tag == "contributor" and text and not res["translator"]:
                    res["translator"] = text
                elif tag == "publisher" and text and not res["publisher"]:
                    res["publisher"] = text
                elif tag == "date" and text and not res["pub_year"]:
                    m_yr = re.search(r"(19\d\d|20\d\d)", text)
                    if m_yr:
                        res["pub_year"] = m_yr.group(1)
                elif tag == "description" and text and not res["description"]:
                    res["description"] = strip_html(text)
                elif tag == "subject" and text:
                    tags.append(text)
                elif tag == "identifier" and text:
                    scheme = " ".join(str(v) for v in elem.attrib.values()).lower()
                    clean_id = re.sub(r"[^0-9Xx]", "", text)
                    if ("isbn" in scheme or len(clean_id) in (10, 13)) and clean_id.startswith(("978", "979", "7")):
                        res["isbn"] = clean_id
                    elif "douban" in scheme and clean_id:
                        res["douban_id"] = clean_id
                elif tag == "meta":
                    name_attr = (elem.attrib.get("name") or "").lower()
                    content_attr = elem.attrib.get("content") or ""
                    if name_attr == "cover" and content_attr:
                        cover_id = content_attr
                    elif name_attr == "calibre:series" and content_attr:
                        res["series"] = content_attr
                elif tag == "item":
                    iid = elem.attrib.get("id") or ""
                    href = elem.attrib.get("href") or ""
                    mtype = (elem.attrib.get("media-type") or "").lower()
                    props = (elem.attrib.get("properties") or "").lower()
                    if iid and href:
                        manifest[iid] = (href, mtype, props)
                elif tag == "itemref":
                    idref = elem.attrib.get("idref") or ""
                    if idref:
                        spine_ids.append(idref)

            if authors:
                clean_auths = [
                    re.sub(r"(?:-SanQiu)?\.(?:mobi|epub|azw3|pdf)$", "", a, flags=re.IGNORECASE).strip()
                    for a in authors
                    if a and len(a.strip()) <= 45 and not re.search(r"\d{5,}", a)
                ]
                uniq_a = list(dict.fromkeys(a for a in clean_auths if a))
                res["author"] = " / ".join(uniq_a[:3]) + (" 等" if len(uniq_a) > 3 else "")
            if tags:
                res["tags"] = list(dict.fromkeys(tags))[:12]

            if cover_out_path:
                cover_href = None
                if cover_id and cover_id in manifest:
                    cover_href = manifest[cover_id][0]
                if not cover_href:
                    for iid, (href, mtype, props) in manifest.items():
                        if "cover-image" in props or (
                            mtype.startswith("image/") and "cover" in (iid + href).lower()
                        ):
                            cover_href = href
                            break
                if not cover_href:
                    for iid, (href, mtype, props) in manifest.items():
                        if mtype.startswith("image/"):
                            cover_href = href
                            break
                if cover_href:
                    full_cover = resolve_href(cover_href)
                    if full_cover in namelist:
                        img_data = safe_zip_read(zf, full_cover)
                        if len(img_data) > 500:
                            with open(cover_out_path, "wb") as cf:
                                cf.write(img_data)
                            res["cover_saved"] = True

            toc_items: List[str] = []
            ncx_files = [
                resolve_href(href)
                for _, (href, mtype, props) in manifest.items()
                if "ncx" in mtype or href.lower().endswith(".ncx") or "nav" in props
            ]
            for nf in ncx_files:
                if nf in namelist:
                    try:
                        toc_raw = safe_zip_read(zf, nf).decode("utf-8", errors="ignore")
                        for m_lbl in re.finditer(r"<text[^>]*>(.*?)</text>|<a[^>]*>(.*?)</a>", toc_raw, re.DOTALL | re.IGNORECASE):
                            lbl = strip_html(m_lbl.group(1) or m_lbl.group(2) or "").strip()
                            if 1 <= len(lbl) <= 60 and lbl not in toc_items:
                                toc_items.append(lbl)
                            if len(toc_items) >= 45:
                                break
                    except Exception as err:
                        logger.debug("Failed reading TOC from %s: %s", nf, err)
                if toc_items:
                    break
            res["toc"] = toc_items

            if not res["pub_year"]:
                for nf in namelist:
                    nfl = nf.lower()
                    if nfl.endswith((".xhtml", ".html", ".htm")) and any(k in nfl for k in ["copy", "colophon", "title", "info", "imprint"]):
                        try:
                            raw_h = safe_zip_read(zf, nf).decode("utf-8", errors="ignore")
                            m_cp = re.findall(r"(?:copyright\s*(?:©|\(c\))?|©\s*|published\s+in\s+|第一版|第1版|出版时间[:：]?\s*|版次|初版)(?:[^\d]{0,25})([12]\d{3})", raw_h, re.IGNORECASE)
                            if m_cp:
                                for c in m_cp:
                                    if 1850 <= int(c) <= 2030:
                                        res["pub_year"] = c
                                        break
                            if res["pub_year"]:
                                break
                        except Exception as err:
                            logger.debug("Failed scanning year from %s: %s", nf, err)

            text_samples: List[str] = []
            total_chars = 0
            for sid in spine_ids[:14]:
                if sid not in manifest:
                    continue
                href, mtype, _ = manifest[sid]
                if "html" not in mtype and not href.lower().endswith((".html", ".xhtml", ".htm", ".xml")):
                    continue
                if re.search(r"(?:cover|copyright|titlepage|dedication|acknow)", href.lower()):
                    continue
                full_doc = resolve_href(href)
                if full_doc in namelist:
                    try:
                        raw_doc = safe_zip_read(zf, full_doc).decode("utf-8", errors="ignore")
                        txt = strip_html(raw_doc)
                        if len(txt) <= 80:
                            continue
                        if len(txt) < 400 and re.search(
                            r"(?:作者授权|电子版发行|版权所有|侵权必究|图书在版编目|由衷感谢|献给)",
                            txt,
                        ):
                            continue
                        text_samples.append(txt[:1600])
                        total_chars += len(txt)
                        if total_chars >= 3600:
                            break
                    except Exception as err:
                        logger.debug("Failed reading spine %s: %s", full_doc, err)
            res["text_sample"] = "\n".join(text_samples)[:3800]
    except Exception as err:
        logger.debug("Failed parsing EPUB %s: %s", file_path, err)
    return res
