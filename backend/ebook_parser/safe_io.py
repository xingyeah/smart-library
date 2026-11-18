import os
import zipfile
import xml.etree.ElementTree as ET
from typing import Any

MAX_ZIP_MEMBER = 50 * 1024 * 1024


def safe_zip_read(zf: zipfile.ZipFile, name: str, cap: int = MAX_ZIP_MEMBER) -> bytes:
    """Read one archive member, refusing members that decompress past cap (zip bombs)."""
    if zf.getinfo(name).file_size > cap:
        raise ValueError(f"{name}: {zf.getinfo(name).file_size} bytes is too large")
    return zf.read(name)


def safe_xml(data: Any) -> ET.Element:
    """Parse ebook XML; entity declarations are refused because this expat does not cap their expansion."""
    raw = data if isinstance(data, bytes) else str(data).encode("utf-8", errors="ignore")
    if b"<!ENTITY" in raw.replace(b"\x00", b""):
        raise ValueError("XML entity declarations are not allowed")
    return ET.fromstring(data)
