"""Traditional → simplified Chinese through the system ICU transform (CoreFoundation), for search keys only."""

import ctypes
import ctypes.util
import functools
import logging
import sys
from typing import Any, Dict, List, Optional

CF_UTF8 = 0x08000100
ICU_TRANSFORM_ID = "Traditional-Simplified"
LOGGER = logging.getLogger(__name__)


def _bind_core_foundation(cf: Any) -> None:
    cf.CFStringCreateWithBytes.restype = ctypes.c_void_p
    cf.CFStringCreateWithBytes.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32, ctypes.c_bool]
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
    cf.CFRelease.restype = None
    cf.CFRelease.argtypes = [ctypes.c_void_p]


def _load_core_foundation() -> Optional[Any]:
    """CoreFoundation, or None off macOS or when it cannot be loaded (search then keeps traditional text as is)."""
    if sys.platform != "darwin":
        return None
    path = ctypes.util.find_library("CoreFoundation")
    if not path:
        return None
    try:
        cf = ctypes.CDLL(path)
        _bind_core_foundation(cf)
    except (OSError, AttributeError):
        LOGGER.warning("CoreFoundation unavailable; traditional-Chinese queries are not simplified", exc_info=True)
        return None
    return cf


def _create_cf_string(text: str) -> Optional[int]:
    data = text.encode("utf-8")
    return _CF.CFStringCreateWithBytes(None, data, len(data), CF_UTF8, False)


_CF = _load_core_foundation()
_TRANSFORM = _create_cf_string(ICU_TRANSFORM_ID) if _CF else None


def _read_cf_string(cf_string: int) -> Optional[str]:
    size = _CF.CFStringGetMaximumSizeForEncoding(_CF.CFStringGetLength(cf_string), CF_UTF8) + 1
    buffer = ctypes.create_string_buffer(size)
    if not _CF.CFStringGetCString(cf_string, buffer, size, CF_UTF8):
        return None
    return buffer.value.decode("utf-8")


@functools.lru_cache(maxsize=4096)
def to_simplified(text: str) -> str:
    """`text` with traditional characters made simplified; unchanged where the system transform is unavailable."""
    # A NUL would end the C string read back, dropping the rest of the query
    if not text or _CF is None or not _TRANSFORM or "\x00" in text:
        return text
    source = _create_cf_string(text)
    if not source:
        return text
    mutable = None
    try:
        mutable = _CF.CFStringCreateMutableCopy(None, 0, source)
        if not mutable or not _CF.CFStringTransform(mutable, None, _TRANSFORM, False):
            LOGGER.debug("ICU %s transform failed", ICU_TRANSFORM_ID)
            return text
        return _read_cf_string(mutable) or text
    finally:
        _CF.CFRelease(source)
        if mutable:
            _CF.CFRelease(mutable)


def original_spelling(term: str, original: str, simplified: str) -> str:
    """The span of `original` that `term` (found in `simplified`) came from; '' when it is not there or is unchanged."""
    start = simplified.lower().find(term.lower())
    if start < 0:
        return ""
    variant = original[start:start + len(term)]
    return "" if variant.lower() == term.lower() else variant


def _variants(terms: List[str], original: str, simplified: str) -> List[str]:
    found = (original_spelling(term, original, simplified) for term in terms)
    return list(dict.fromkeys(v for v in found if v and v not in terms))


def with_traditional_variants(intent: Dict[str, Any], original: str, simplified: str) -> Dict[str, Any]:
    """Adds the original spellings of the query's terms, title and exclusions (the transform keeps one char per char).

    Variants go first in `synonym_terms`: callers read only its head ([:6]), and a variant is what finds books
    written in traditional script.
    """
    if len(original) != len(simplified):
        return intent
    variants = _variants(intent["primary_terms"], original, simplified)
    intent["synonym_terms"] = variants + [s for s in intent["synonym_terms"] if s not in variants]
    intent["negative_terms"] = intent["negative_terms"] + _variants(intent["negative_terms"], original, simplified)
    title = intent.get("explicit_title") or ""
    intent["explicit_title_variant"] = original_spelling(title, original, simplified) if title else ""
    return intent
