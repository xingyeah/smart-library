import logging
import os
import re
import struct
from typing import Any, Dict, List, Optional, Tuple
from parser import _decompress_palmdoc, strip_html
from book_reader.sanitizer import (
    _extract_heading_from_html,
    sanitize_and_format_book_html,
)

logger = logging.getLogger(__name__)


def _get_mobi_trailing_size(data: bytes, extra_flags: int) -> int:
    """Compute trailing multibyte overlap and extra record bytes per MOBI specification."""
    size = 0
    f_val = extra_flags >> 1
    while f_val:
        if f_val & 1:
            v = 0
            shift = 0
            idx = len(data) - 1 - size
            while idx >= 0:
                b = data[idx]
                v |= (b & 0x7F) << shift
                shift += 7
                idx -= 1
                if b & 0x80:
                    break
            size += v
        f_val >>= 1
    if (extra_flags & 1) and (len(data) - size > 0):
        size += (data[len(data) - 1 - size] & 0x03) + 1
    return size


class HuffCdicDecompressor:
    """Native pure-Python HUFF/CDIC decompressor for AZW3 / KF8 / MOBI (compression == 17480)."""

    def __init__(self, huff_rec: bytes, cdic_recs: List[bytes]) -> None:
        if len(huff_rec) < 24 or huff_rec[:4] != b"HUFF":
            raise ValueError("Invalid HUFF header")
        off1, off2 = struct.unpack(">II", huff_rec[8:16])
        self.dict1 = struct.unpack(">256I", huff_rec[off1 : off1 + 1024])
        self.dict2 = struct.unpack(">64I", huff_rec[off2 : off2 + 256])
        self.dicts: List[Tuple[bytes, bool]] = []
        for cdic in cdic_recs:
            if len(cdic) < 16 or cdic[:4] != b"CDIC":
                continue
            phrases, bits = struct.unpack(">II", cdic[8:16])
            n = min(1 << bits, phrases - len(self.dicts))
            if n <= 0:
                continue
            off_table = struct.unpack(f">{n}H", cdic[16 : 16 + n * 2])
            for off in off_table:
                blen = struct.unpack(">H", cdic[16 + off : 18 + off])[0]
                slice_bytes = cdic[18 + off : 18 + off + (blen & 0x7FFF)]
                self.dicts.append((slice_bytes, bool(blen & 0x8000)))

    def unpack(self, data: bytes, depth: int = 0) -> bytes:
        if depth > 32 or not data:
            return b""
        out = bytearray()
        bits_left = len(data) * 8
        padded = data + b"\x00" * 8
        pos = 0
        dict1 = self.dict1
        dict2 = self.dict2
        dicts = self.dicts
        n_dicts = len(dicts)
        while bits_left > 0:
            byte_idx = pos >> 3
            bit_off = pos & 7
            x = struct.unpack(">Q", padded[byte_idx : byte_idx + 8])[0]
            w = ((x << bit_off) & 0xFFFFFFFFFFFFFFFF) >> 32
            entry = dict1[w >> 24]
            codelen = entry & 0x1F
            term = bool(entry & 0x80)
            maxcode = entry >> 8
            if not term:
                while codelen < 32 and (w >> (32 - codelen)) < dict2[(codelen - 1) * 2]:
                    codelen += 1
                maxcode = dict2[(codelen - 1) * 2 + 1]
            if codelen == 0:
                break
            pos += codelen
            bits_left -= codelen
            if bits_left < 0:
                break
            r = maxcode - (w >> (32 - codelen))
            if 0 <= r < n_dicts:
                slice_bytes, flag = dicts[r]
                if flag:
                    out.extend(slice_bytes)
                else:
                    out.extend(self.unpack(slice_bytes, depth + 1))
        return bytes(out)


def build_mobi_azw3_structure(
    book_id: int,
    fpath: str,
    cache_get: Any,
    cache_put: Any,
) -> Dict[str, Any]:
    """Decompress and parse MOBI / AZW3 / KF8 into structured HTML chapters and hierarchical TOC."""
    mtime = os.path.getmtime(fpath)
    cached = cache_get(book_id, mtime)
    if cached is not None:
        return cached

    with open(fpath, "rb") as f:
        header = f.read(78)
        if len(header) < 78:
            raise ValueError("Invalid MOBI/AZW3 header")
        num_records = struct.unpack(">H", header[76:78])[0]
        if num_records == 0 or num_records > 40000:
            raise ValueError("Invalid record count")
        rec_info = f.read(num_records * 8)
        offsets = [struct.unpack(">I", rec_info[i * 8 : i * 8 + 4])[0] for i in range(num_records)]
        f.seek(0, os.SEEK_END)
        offsets.append(f.tell())

        def read_rec(idx: int) -> bytes:
            if idx < 0 or idx >= num_records:
                return b""
            s, e = offsets[idx], offsets[idx + 1]
            if e <= s or e - s > 25 * 1024 * 1024:
                return b""
            f.seek(s)
            return f.read(e - s)

        rec0 = read_rec(0)
        if len(rec0) < 132:
            raise ValueError("MOBI record 0 too short")

        compression = struct.unpack(">H", rec0[0:2])[0]
        text_rec_count = struct.unpack(">H", rec0[8:10])[0]
        encoding_code = struct.unpack(">I", rec0[28:32])[0]
        encoding = "utf-8" if encoding_code == 65001 else "cp1252"
        first_image_idx = struct.unpack(">I", rec0[108:112])[0] if len(rec0) >= 112 else 0
        extra_flags = struct.unpack(">H", rec0[242:244])[0] if len(rec0) >= 244 else 0

        huff_reader: Optional[HuffCdicDecompressor] = None
        if compression == 17480 and len(rec0) >= 120:
            huff_idx, huff_cnt = struct.unpack(">II", rec0[112:120])
            if 0 < huff_idx < num_records and huff_cnt > 1:
                huff_rec = read_rec(huff_idx)
                cdic_recs = [read_rec(huff_idx + i) for i in range(1, huff_cnt)]
                huff_reader = HuffCdicDecompressor(huff_rec, cdic_recs)

        chunks: List[bytes] = []
        max_recs = min(text_rec_count, num_records - 1, 2500)
        for tidx in range(1, max_recs + 1):
            raw = read_rec(tidx)
            if not raw:
                continue
            ts = _get_mobi_trailing_size(raw, extra_flags)
            if 0 < ts < len(raw):
                raw = raw[:-ts]
            if compression == 1:
                chunks.append(raw)
            elif compression == 2:
                chunks.append(_decompress_palmdoc(raw))
            elif compression == 17480 and huff_reader is not None:
                chunks.append(huff_reader.unpack(raw))

        raw_stream = b"".join(chunks)
        try:
            full_html = raw_stream.decode(encoding, errors="ignore")
        except Exception:
            full_html = raw_stream.decode("utf-8", errors="ignore")

        chapters: List[Dict[str, Any]] = []
        toc_list: List[Dict[str, Any]] = []

        xml_docs = [d for d in re.split(r"(?=<\?xml\s+version=)", full_html) if len(d.strip()) > 30]
        if len(xml_docs) >= 3:
            for d in xml_docs:
                plain = strip_html(d).strip()
                has_img = bool(re.search(r"<(?:img|image)\b", d, re.IGNORECASE))
                if len(plain) < 5 and not has_img:
                    continue
                c_idx = len(chapters)
                doc_headings: List[Tuple[int, str, str]] = []
                for m_h in re.finditer(r"<(h[1-4])([^>]*)>(.*?)</\1>", d, re.DOTALL | re.IGNORECASE):
                    htag = m_h.group(1).lower()
                    hattrs = m_h.group(2) or ""
                    hinner = m_h.group(3) or ""
                    htxt = " ".join(strip_html(hinner).split()).strip()
                    if not htxt:
                        mt = re.search(r'title=["\']([^"\']+)["\']', hattrs, re.IGNORECASE)
                        if mt:
                            htxt = mt.group(1).strip()
                    if not htxt or len(htxt) > 90:
                        continue
                    id_m = re.search(r'\bid=["\']([^"\']+)["\']', hattrs, re.IGNORECASE)
                    aid_m = re.search(r'\baid=["\']([^"\']+)["\']', hattrs, re.IGNORECASE)
                    h_anchor = id_m.group(1) if id_m else (f"aid-{aid_m.group(1)}" if aid_m else "")
                    h_level = 0 if htag in ("h1", "h2") else 1
                    doc_headings.append((h_level, htxt, h_anchor))

                d_clean = re.sub(
                    r'<h([1-4])\b[^>]*?\btitle=["\']([^"\']+)["\'][^>]*>\s*</h\1>',
                    r"<h\1>\2</h\1>",
                    d,
                    flags=re.IGNORECASE,
                )
                chap_title = doc_headings[0][1] if doc_headings else (_extract_heading_from_html(d_clean) or f"第 {c_idx + 1} 节")
                formatted = sanitize_and_format_book_html(d_clean, book_id, is_mobi=True, mobi_embed_base=first_image_idx)
                if not formatted or (len(strip_html(formatted).strip()) < 5 and "<img" not in formatted.lower()):
                    continue
                chapters.append(
                    {
                        "index": c_idx,
                        "title": chap_title,
                        "html": formatted,
                    }
                )
                if doc_headings:
                    for h_level, htxt, h_anchor in doc_headings:
                        toc_list.append(
                            {
                                "title": htxt,
                                "chapter_idx": c_idx,
                                "anchor": h_anchor,
                                "level": h_level,
                            }
                        )
                else:
                    toc_list.append(
                        {
                            "title": chap_title,
                            "chapter_idx": c_idx,
                            "anchor": "",
                            "level": 0,
                        }
                    )
        else:
            fpos_entries: List[Tuple[int, str]] = []
            seen_pos = set()

            toc_scope_html = full_html[:120000]
            ref_m = re.search(
                r'<reference\b[^>]*?\btype=["\']?toc["\']?[^>]*?\bfilepos=["\']?0*(\d+)["\']?',
                full_html[:15000],
                re.IGNORECASE,
            )
            if ref_m:
                toc_byte_pos = int(ref_m.group(1))
                if 0 < toc_byte_pos < len(raw_stream):
                    toc_raw_slice = raw_stream[toc_byte_pos : toc_byte_pos + 65000].decode(encoding, errors="ignore")
                    pb_m = re.search(r"<mbp:pagebreak\s*/?>", toc_raw_slice, re.IGNORECASE)
                    toc_scope_html = toc_raw_slice[: pb_m.start()] if pb_m else toc_raw_slice

            for m_fp in re.finditer(
                r'<a\b[^>]*?\bfilepos=["\']?0*(\d+)["\']?[^>]*>(.*?)</a>',
                toc_scope_html,
                re.DOTALL | re.IGNORECASE,
            ):
                pos_val = int(m_fp.group(1))
                lbl = " ".join(strip_html(m_fp.group(2)).split()).strip()
                if re.match(r"^[\[\(（【\*†‡]?[0-9①-⑳a-zA-Z]{1,3}[\]\)）】\*]?$", lbl):
                    continue
                if lbl and 2 <= len(lbl) <= 85 and 0 < pos_val < len(raw_stream):
                    if pos_val not in seen_pos:
                        seen_pos.add(pos_val)
                        fpos_entries.append((pos_val, lbl))

            fpos_entries.sort(key=lambda x: x[0])

            if len(fpos_entries) >= 3:
                if fpos_entries[0][0] > 800:
                    front_raw = raw_stream[: fpos_entries[0][0]].decode(encoding, errors="ignore")
                    if len(strip_html(front_raw).strip()) > 40:
                        chapters.append(
                            {
                                "index": 0,
                                "title": "封面与前言导览",
                                "html": sanitize_and_format_book_html(front_raw, book_id, is_mobi=True),
                            }
                        )
                        toc_list.append({"title": "封面与前言导览", "chapter_idx": 0, "anchor": "", "level": 0})

                for i, (start_b, lbl) in enumerate(fpos_entries):
                    end_b = fpos_entries[i + 1][0] if i + 1 < len(fpos_entries) else len(raw_stream)
                    if end_b <= start_b:
                        continue
                    seg_html = raw_stream[start_b:end_b].decode(encoding, errors="ignore")
                    if len(strip_html(seg_html).strip()) < 5 and "<img" not in seg_html.lower():
                        continue
                    c_idx = len(chapters)
                    lvl = 0 if re.match(r"^(?:第[一二三四五六七八九十\d]+[部篇卷]|Part\s+)", lbl, re.IGNORECASE) else (
                        1 if any(t["level"] == 0 and re.match(r"^(?:第[一二三四五六七八九十\d]+[部篇卷]|Part\s+)", t["title"], re.IGNORECASE) for t in toc_list) else 0
                    )
                    chapters.append(
                        {
                            "index": c_idx,
                            "title": lbl,
                            "html": sanitize_and_format_book_html(seg_html, book_id, is_mobi=True),
                        }
                    )
                    toc_list.append(
                        {
                            "title": lbl,
                            "chapter_idx": c_idx,
                            "anchor": "",
                            "level": lvl,
                        }
                    )
            else:
                parts = re.split(r"<mbp:pagebreak\s*/?>", full_html, flags=re.IGNORECASE)
                for p in parts:
                    plain = strip_html(p).strip()
                    if len(plain) < 20 and "<img" not in p.lower():
                        continue
                    c_idx = len(chapters)
                    title = _extract_heading_from_html(p) or f"第 {c_idx + 1} 节"
                    chapters.append(
                        {
                            "index": c_idx,
                            "title": title,
                            "html": sanitize_and_format_book_html(p, book_id, is_mobi=True),
                        }
                    )
                    toc_list.append(
                        {
                            "title": title,
                            "chapter_idx": c_idx,
                            "anchor": "",
                            "level": 0,
                        }
                    )

        for idx, t in enumerate(toc_list):
            t["toc_id"] = idx

        struct_data = {
            "mode": "mobi",
            "chapters": chapters,
            "toc": toc_list,
            "first_image_idx": first_image_idx,
        }
        cache_put(book_id, mtime, struct_data)
        return struct_data
