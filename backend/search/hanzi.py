"""Traditional → simplified Chinese through the system ICU transform (CoreFoundation), for search keys only."""

import ctypes
import ctypes.util
import functools
import logging
import sys
from typing import Any, Dict

CF_UTF8 = 0x08000100
ICU_TRANSFORM_ID = b"Traditional-Simplified"


def _load_core_foundation():
    if sys.platform != "darwin":
        return None
    path = ctypes.util.find_library("CoreFoundation")
    if not path:
        return None
    cf = ctypes.CDLL(path)
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFStringCreateMutableCopy.restype = ctypes.c_void_p
    cf.CFStringCreateMutableCopy.argtypes = [ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p]
    cf.CFStringTransform.restype = ctypes.c_bool
    cf.CFStringTransform.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
    cf.CFStringGetLength.restype = ctypes.c_long
    cf.CFStringGetLength.argtypes = [ctypes.c_void_p]
    cf.CFStringGetMaximumSizeForEncoding.restype = ctypes.c_long
    cf.CFStringGetMaximumSizeForEncoding.argtypes = [ctypes.c_long, ctypes.c_uint32]
    cf.CFStringGetCString.restype = ctypes.c_bool
    cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    return cf


_CF = _load_core_foundation()
_TRANSFORM = _CF.CFStringCreateWithCString(None, ICU_TRANSFORM_ID, CF_UTF8) if _CF else None


@functools.lru_cache(maxsize=4096)
def to_simplified(text: str) -> str:
    """`text` with traditional characters made simplified; unchanged where the system transform is unavailable."""
    if not text or _CF is None or not _TRANSFORM:
        return text
    source = _CF.CFStringCreateWithCString(None, text.encode("utf-8"), CF_UTF8)
    if not source:
        return text
    mutable = _CF.CFStringCreateMutableCopy(None, 0, source)
    try:
        if not mutable or not _CF.CFStringTransform(mutable, None, _TRANSFORM, False):
            logging.getLogger(__name__).debug("ICU %s transform failed", ICU_TRANSFORM_ID)
            return text
        size = _CF.CFStringGetMaximumSizeForEncoding(_CF.CFStringGetLength(mutable), CF_UTF8) + 1
        buffer = ctypes.create_string_buffer(size)
        if not _CF.CFStringGetCString(mutable, buffer, size, CF_UTF8):
            return text
        return buffer.value.decode("utf-8")
    finally:
        _CF.CFRelease(source)
        if mutable:
            _CF.CFRelease(mutable)


def with_traditional_variants(intent: Dict[str, Any], original: str, simplified: str) -> Dict[str, Any]:
    """Adds, as synonyms, the original spelling of each primary term (the transform keeps one character per character)."""
    if len(original) != len(simplified):
        return intent
    synonyms = intent["synonym_terms"]
    for term in intent["primary_terms"]:
        start = simplified.find(term)
        variant = original[start:start + len(term)] if start >= 0 else ""
        if variant and variant != term and variant not in synonyms:
            synonyms.append(variant)
    return intent
