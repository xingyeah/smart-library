import logging
import os
import re
import struct
from typing import Any, Dict
from ebook_parser.filenames import strip_html

logger = logging.getLogger(__name__)


def decompress_palmdoc(data: bytes) -> bytes:
    """Decompress PalmDOC LZ77 compressed record bytes."""
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        c = data[i]
        i += 1
        if c == 0x00 or (0x09 <= c <= 0x7F):
            out.append(c)
        elif 0x01 <= c <= 0x08:
            out.extend(data[i : i + c])
            i += c
        elif 0x80 <= c <= 0xBF:
            if i >= n:
                break
            c2 = data[i]
            i += 1
            pair = ((c << 8) | c2) & 0x3FFF
            dist = pair >> 3
            length = (pair & 0x07) + 3
            if dist == 0 or dist > len(out):
                continue
            for _ in range(length):
                out.append(out[-dist])
        else:
            out.append(0x20)
            out.append(c ^ 0x80)
    return bytes(out)


def parse_mobi_azw3(file_path: str, cover_out_path: str = "") -> Dict[str, Any]:
    """Native binary parser for MOBI / AZW3 / KF8 headers, EXTH metadata, cover image, and opening text."""
    res: Dict[str, Any] = {
        "title": "",
        "author": "",
        "publisher": "",
        "pub_year": "",
        "isbn": "",
        "douban_id": "",
        "tags": [],
        "description": "",
        "toc": [],
        "text_sample": "",
        "cover_saved": False,
    }
    try:
        with open(file_path, "rb") as f:
            header = f.read(78)
            if len(header) < 78:
                return res
            db_type = header[60:68]
            if db_type not in (b"BOOKMOBI", b"TEXtREAd"):
                return res
            num_records = struct.unpack(">H", header[76:78])[0]
            if num_records == 0 or num_records > 30000:
                return res
            rec_info = f.read(num_records * 8)
            offsets = [
                struct.unpack(">I", rec_info[i * 8 : i * 8 + 4])[0]
                for i in range(num_records)
            ]
            f.seek(0, os.SEEK_END)
            offsets.append(f.tell())

            def read_rec(idx: int) -> bytes:
                if idx < 0 or idx >= num_records:
                    return b""
                start, end = offsets[idx], offsets[idx + 1]
                if end <= start or end - start > 25 * 1024 * 1024:
                    return b""
                f.seek(start)
                return f.read(end - start)

            rec0 = read_rec(0)
            if len(rec0) < 132:
                return res

            compression = struct.unpack(">H", rec0[0:2])[0]
            text_rec_count = struct.unpack(">H", rec0[8:10])[0]
            encoding_code = struct.unpack(">I", rec0[28:32])[0]
            encoding = "utf-8" if encoding_code == 65001 else "cp1252"

            full_name_offset = struct.unpack(">I", rec0[84:88])[0]
            full_name_len = struct.unpack(">I", rec0[88:92])[0]
            if full_name_offset + full_name_len <= len(rec0) and full_name_len > 0:
                res["title"] = rec0[full_name_offset : full_name_offset + full_name_len].decode(
                    encoding, errors="ignore"
                ).strip()

            first_image_idx = struct.unpack(">I", rec0[108:112])[0] if len(rec0) >= 112 else 0
            exth_flag = struct.unpack(">I", rec0[128:132])[0]
            mobi_header_len = struct.unpack(">I", rec0[20:24])[0]
            exth_offset = 16 + mobi_header_len

            cover_rec_offset = None
            if (exth_flag & 0x40) and exth_offset + 12 <= len(rec0):
                if rec0[exth_offset : exth_offset + 4] == b"EXTH":
                    exth_count = struct.unpack(">I", rec0[exth_offset + 8 : exth_offset + 12])[0]
                    pos = exth_offset + 12
                    authors = []
                    tags = []
                    for _ in range(min(exth_count, 200)):
                        if pos + 8 > len(rec0):
                            break
                        rtype, rlen = struct.unpack(">II", rec0[pos : pos + 8])
                        if rlen < 8 or pos + rlen > len(rec0):
                            break
                        rval = rec0[pos + 8 : pos + rlen]
                        pos += rlen
                        if rtype == 100:
                            authors.append(rval.decode(encoding, errors="ignore").strip())
                        elif rtype == 101:
                            res["publisher"] = rval.decode(encoding, errors="ignore").strip()
                        elif rtype == 103:
                            res["description"] = strip_html(rval.decode(encoding, errors="ignore"))
                        elif rtype == 104:
                            res["isbn"] = re.sub(r"[^0-9Xx]", "", rval.decode(encoding, errors="ignore"))
                        elif rtype == 105:
                            tags.append(rval.decode(encoding, errors="ignore").strip())
                        elif rtype == 106:
                            res["pub_year"] = rval.decode(encoding, errors="ignore")[:4]
                        elif rtype == 503:
                            t503 = rval.decode(encoding, errors="ignore").strip()
                            if t503:
                                res["title"] = t503
                        elif rtype == 201 and len(rval) == 4:
                            cover_rec_offset = struct.unpack(">I", rval)[0]
                    if authors:
                        clean_auths = [
                            re.sub(r"(?:-SanQiu)?\.(?:mobi|epub|azw3|pdf)$", "", a, flags=re.IGNORECASE).strip()
                            for a in authors
                            if a and len(a.strip()) <= 45 and not re.search(r"\d{5,}", a)
                        ]
                        uniq_a = list(dict.fromkeys(a for a in clean_auths if a))
                        res["author"] = " / ".join(uniq_a[:3]) + (" 等" if len(uniq_a) > 3 else "")
                    if tags:
                        res["tags"] = list(dict.fromkeys(t for t in tags if t))

            if cover_out_path and first_image_idx > 0 and first_image_idx < num_records:
                candidates = []
                if cover_rec_offset is not None and cover_rec_offset < 1000:
                    candidates.append(first_image_idx + cover_rec_offset)
                for idx in range(first_image_idx, min(num_records, first_image_idx + 8)):
                    if idx not in candidates:
                        candidates.append(idx)
                for c_idx in candidates:
                    img_bytes = read_rec(c_idx)
                    if len(img_bytes) > 1000 and (
                        img_bytes.startswith(b"\xff\xd8\xff")
                        or img_bytes.startswith(b"\x89PNG")
                    ):
                        with open(cover_out_path, "wb") as cf:
                            cf.write(img_bytes)
                        res["cover_saved"] = True
                        break

            if compression in (1, 2) and text_rec_count > 0:
                chunks = []
                for tidx in range(1, min(text_rec_count + 1, 6)):
                    raw = read_rec(tidx)
                    if not raw:
                        continue
                    dec = decompress_palmdoc(raw) if compression == 2 else raw
                    chunks.append(dec)
                joined = b"".join(chunks).decode(encoding, errors="ignore")
                clean_txt = strip_html(joined)
                res["text_sample"] = clean_txt[:3500]
                toc_lines = []
                for ln in clean_txt.splitlines():
                    ln = ln.strip()
                    if 2 <= len(ln) <= 45 and re.match(
                        r"^(?:第[0-9一二三四五六七八九十百]+[章回节篇卷部]|Chapter\s+\d+|序言|前言|导读|引言|目录|结语|后记|\d+[\.、]\s*\S+)",
                        ln,
                        re.IGNORECASE,
                    ):
                        if ln not in toc_lines:
                            toc_lines.append(ln)
                        if len(toc_lines) >= 30:
                            break
                res["toc"] = toc_lines
    except Exception as err:
        logger.debug("Failed parsing MOBI/AZW3 %s: %s", file_path, err)
    return res
