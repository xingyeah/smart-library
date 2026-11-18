import logging
import re
from typing import List
from native_bridge import get_native_bridge

logger = logging.getLogger(__name__)


def build_cjk_search_tokens(text: str, include_pinyin: bool = False) -> str:
    """Create space-separated CJK unigram + bigram + Latin + Pinyin tokens so SQLite FTS5 unicode61 matches queries."""
    if include_pinyin:
        return build_cjk_pinyin_search_tokens(text)
    return _build_cjk_tokens_core(text, with_pinyin=False)


def build_cjk_pinyin_search_tokens(text: str) -> str:
    """Create space-separated CJK unigram + bigram + Latin + Pinyin tokens."""
    return _build_cjk_tokens_core(text, with_pinyin=True)


def _build_cjk_tokens_core(text: str, with_pinyin: bool = False) -> str:
    if not text:
        return ""
    tokens: List[str] = []
    cjk_runs = re.findall(r"[\u4e00-\u9fff]+", text[:1500])
    for run in cjk_runs:
        for ch in run:
            tokens.append(ch)
        for i in range(len(run) - 1):
            tokens.append(run[i : i + 2])
    latin_words = re.findall(r"[a-zA-Z0-9]{2,}", text[:1500].lower())
    tokens.extend(latin_words)

    if with_pinyin:
        try:
            py = get_native_bridge().to_pinyin(text[:200])
            if py:
                tokens.append(py)
        except Exception as err:
            logger.debug("Pinyin generation skipped for text: %s", err)

    seen = set()
    uniq = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return " ".join(uniq)
