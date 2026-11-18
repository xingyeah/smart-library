import html
import json
import logging
import posixpath
import re
import urllib.parse
from typing import Dict, List, Optional
from parser import strip_html

logger = logging.getLogger(__name__)


def resolve_zip_path(base_dir: str, href: str) -> str:
    """Resolve relative href inside EPUB zip against base_dir."""
    href_clean = urllib.parse.unquote(href.split("#")[0].strip())
    if not href_clean:
        return ""
    if href_clean.startswith("/"):
        return href_clean.lstrip("/")
    if not base_dir or base_dir == ".":
        norm = posixpath.normpath(href_clean)
    else:
        norm = posixpath.normpath(posixpath.join(base_dir, href_clean))
    return norm.lstrip("/")


def _js_string(value: str) -> str:
    return html.escape(json.dumps(value))


def _extract_heading_from_html(raw_html: str) -> str:
    """Extract the most representative chapter heading from an HTML document."""
    body = re.sub(r"<head[^>]*>.*?</head>", "", raw_html, flags=re.DOTALL | re.IGNORECASE)

    for tag in ("h1", "h2", "h3", "h4"):
        for m in re.finditer(rf"<{tag}([^>]*)>(.*?)</{tag}>", body, re.DOTALL | re.IGNORECASE):
            attrs, inner = m.group(1), m.group(2)
            txt = " ".join(strip_html(inner).split()).strip()
            if not txt:
                mt = re.search(r'title=["\']([^"\']+)["\']', attrs, re.IGNORECASE)
                if mt:
                    txt = mt.group(1).strip()
            if 1 <= len(txt) <= 85:
                return txt

    title_m = re.search(r"<title[^>]*>(.*?)</title>", raw_html, re.DOTALL | re.IGNORECASE)
    if title_m:
        t_txt = " ".join(strip_html(title_m.group(1)).split()).strip()
        if (
            1 <= len(t_txt) <= 80
            and t_txt.lower() not in ("unknown", "contents", "table of contents", "untitled", "chapter", "calibre")
            and not re.match(r"^(?:part|section|chapter|page|index|split)[\s_\-]*\d+$", t_txt, re.IGNORECASE)
        ):
            return t_txt

    for m_p in re.finditer(r"<(?:p|div)[^>]*>(.*?)</(?:p|div)>", body[:2500], re.DOTALL | re.IGNORECASE):
        p_txt = " ".join(strip_html(m_p.group(1)).split()).strip()
        if 2 <= len(p_txt) <= 60:
            return p_txt

    return ""


def sanitize_and_format_book_html(
    raw_html: str,
    book_id: int,
    doc_dir: str = "",
    href_to_spine: Optional[Dict[str, int]] = None,
    is_mobi: bool = False,
    mobi_embed_base: int = 0,
) -> str:
    """
    Transform raw EPUB/MOBI/AZW3 chapter XHTML into clean, semantic, typographically rich HTML.
    """
    if not raw_html:
        return ""

    content = raw_html
    content = re.sub(r"<\?xml[^>]*\?>", "", content, flags=re.IGNORECASE)
    content = re.sub(r"<!DOCTYPE[^>]*>", "", content, flags=re.IGNORECASE)
    content = re.sub(
        r"<(?:script|style|head|meta|link|iframe|object|embed|guide)[^>]*>.*?</(?:script|style|head|meta|link|iframe|object|embed|guide)>",
        "",
        content,
        flags=re.DOTALL | re.IGNORECASE,
    )
    content = re.sub(r"</?(?:html|body|meta|link|mbp:pagebreak|mbp:frameset)[^>]*/?>", "", content, flags=re.IGNORECASE)
    content = re.sub(r"</?(?:script|iframe|frame|frameset|object|embed|applet|form|base)\b[^>]*>", "", content, flags=re.IGNORECASE)
    content = re.sub(
        r"</?(?:video|audio|source|track|input|button|select|option|optgroup|textarea|details|dialog|menu|menuitem|keygen|math|noscript|template|slot|plaintext|xmp|canvas|marquee|portal)\b[^>]*>",
        "",
        content,
        flags=re.DOTALL | re.IGNORECASE,
    )
    content = re.sub(r"\son[a-z]+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", "", content, flags=re.IGNORECASE)

    if is_mobi:
        content = re.sub(
            r'<p[^>]*>\s*<font[^>]*size=["\']?[67]["\']?[^>]*>\s*(?:<b>)?(.*?)(?:</b>)?\s*</font>\s*</p>',
            r"<h2>\1</h2>",
            content,
            flags=re.DOTALL | re.IGNORECASE,
        )
        content = re.sub(
            r'<font[^>]*size=["\']?[67]["\']?[^>]*>\s*<b>(.*?)</b>\s*</font>',
            r"<h2>\1</h2>",
            content,
            flags=re.DOTALL | re.IGNORECASE,
        )
        content = re.sub(
            r'<font[^>]*size=["\']?5["\']?[^>]*>\s*<b>(.*?)</b>\s*</font>',
            r"<h3>\1</h3>",
            content,
            flags=re.DOTALL | re.IGNORECASE,
        )
        content = re.sub(r"</?font[^>]*>", "", content, flags=re.IGNORECASE)

    def _replace_svg_image(m: re.Match) -> str:
        svg_inner = m.group(0)
        href_m = re.search(r'(?:xlink:)?href=["\']([^"\']+)["\']', svg_inner, re.IGNORECASE)
        if not href_m:
            return ""
        raw_src = href_m.group(1).strip()
        return f'<figure class="reader-figure"><img src="{raw_src}" alt="" loading="lazy"/></figure>'

    content = re.sub(r"<svg[^>]*>.*?</svg>", _replace_svg_image, content, flags=re.DOTALL | re.IGNORECASE)

    def _rewrite_img(m: re.Match) -> str:
        tag_str = m.group(0)
        rec_m = re.search(r'recindex=["\']?(\d+)["\']?', tag_str, re.IGNORECASE)
        if rec_m:
            idx = int(rec_m.group(1))
            url = f"/api/books/{book_id}/mobi_image?idx={idx}"
            return f'<img src="{url}" class="reader-img" loading="lazy" alt=""/>'

        src_m = re.search(r'src=["\']([^"\']+)["\']', tag_str, re.IGNORECASE)
        if not src_m:
            return ""
        src = src_m.group(1).strip()
        if src.startswith("kindle:embed:"):
            m_emb = re.match(r"kindle:embed:([0-9A-Za-z]+)", src)
            if m_emb:
                try:
                    idx = int(m_emb.group(1), 32)
                    url = f"/api/books/{book_id}/mobi_image?idx={idx}"
                    return f'<img src="{url}" class="reader-img" loading="lazy" alt=""/>'
                except ValueError as err:
                    logger.debug("Failed parsing kindle:embed base32 index %s: %s", src, err)
            return ""
        if src.startswith(("data:", "/api/")):
            return f'<img src="{src}" class="reader-img" loading="lazy" alt=""/>'
        if "://" in src or src.startswith("//"):
            return ""

        resolved = resolve_zip_path(doc_dir, src)
        if not resolved:
            return ""
        encoded_path = urllib.parse.quote(resolved)
        url = f"/api/books/{book_id}/epub_asset?path={encoded_path}"
        return f'<img src="{url}" class="reader-img" loading="lazy" alt=""/>'

    content = re.sub(r"<img\b[^>]*/?>", _rewrite_img, content, flags=re.IGNORECASE)

    def _rewrite_link(m: re.Match) -> str:
        attrs = m.group(1) or ""
        id_m = re.search(r'\bid=["\']([^"\']+)["\']', attrs, re.IGNORECASE)
        name_m = re.search(r'\bname=["\']([^"\']+)["\']', attrs, re.IGNORECASE)
        anchor_id = (id_m.group(1) if id_m else (name_m.group(1) if name_m else "")).strip()
        id_attr = f' id="{html.escape(anchor_id)}"' if anchor_id else ""

        fpos_m = re.search(r'\bfilepos=["\']?(\d+)["\']?', attrs, re.IGNORECASE)
        if fpos_m:
            fpos = int(fpos_m.group(1))
            return f'<a{id_attr} href="javascript:void(0)" class="reader-internal-link" data-filepos="{fpos}">'

        href_m = re.search(r'\bhref=["\']([^"\']+)["\']', attrs, re.IGNORECASE)
        if not href_m:
            return f"<a{id_attr}>"

        href = href_m.group(1).strip()
        if href.startswith(("http://", "https://", "mailto:")):
            return f'<a{id_attr} href="{html.escape(href)}" target="_blank" rel="noopener noreferrer" class="reader-external-link">'

        if href.startswith("#"):
            frag = href[1:].strip()
            return f'<a{id_attr} href="javascript:void(0)" class="reader-internal-link" onclick="scrollToReaderAnchor({_js_string(frag)})">'

        parts = href.split("#", 1)
        rel_file = parts[0]
        frag = parts[1] if len(parts) > 1 else ""
        resolved_target = resolve_zip_path(doc_dir, rel_file)
        if href_to_spine and resolved_target in href_to_spine:
            target_idx = href_to_spine[resolved_target]
            return (
                f'<a{id_attr} href="javascript:void(0)" class="reader-internal-link" '
                f'onclick="navigateReaderChapter({target_idx}, {_js_string(frag)})">'
            )
        elif frag:
            return f'<a{id_attr} href="javascript:void(0)" class="reader-internal-link" onclick="scrollToReaderAnchor({_js_string(frag)})">'
        return f"<a{id_attr}>"

    content = re.sub(r"<a\b([^>]*)>", _rewrite_link, content, flags=re.IGNORECASE)

    def _clean_tag_attrs(m: re.Match) -> str:
        tag = m.group(1).lower()
        attrs = m.group(2) or ""
        self_closing = m.group(3) or ""
        if tag in ("img", "a"):
            return m.group(0)

        kept = []
        if tag == "figure" and re.search(r'\bclass=["\']reader-figure["\']', attrs):
            kept.append('class="reader-figure"')
        id_m = re.search(r'\bid=["\']([^"\']+)["\']', attrs, re.IGNORECASE)
        if id_m:
            kept.append(f'id="{html.escape(id_m.group(1))}"')
        else:
            aid_m = re.search(r'\baid=["\']([^"\']+)["\']', attrs, re.IGNORECASE)
            if aid_m:
                kept.append(f'id="aid-{html.escape(aid_m.group(1))}"')

        if tag in ("td", "th"):
            for sp in ("colspan", "rowspan"):
                spm = re.search(rf'\b{sp}=["\']?(\d+)["\']?', attrs, re.IGNORECASE)
                if spm:
                    kept.append(f'{sp}="{spm.group(1)}"')

        align_m = re.search(r'text-align\s*:\s*(center|right)', attrs, re.IGNORECASE)
        if not align_m:
            align_m = re.search(r'\balign=["\']?(center|right)["\']?', attrs, re.IGNORECASE)
        if align_m:
            kept.append(f'style="text-align:{align_m.group(1).lower()}"')

        attr_str = (" " + " ".join(kept)) if kept else ""
        return f"<{tag}{attr_str}{self_closing}>"

    content = re.sub(r"<([a-zA-Z][\w:-]*)\b([^>]*?)(/?)>", _clean_tag_attrs, content)
    content = re.sub(r"(?:<br\s*/?>\s*){3,}", "<br/><br/>", content, flags=re.IGNORECASE)
    return content.strip()
