import math
import re
import threading
from collections import Counter
from typing import Dict, List, Optional, Set

from database import get_conn
from search.lexicon import DOMAIN_SYNONYM_MAP

# In-memory Corpus Document Frequency (DF) & IDF cache
_CACHE_LOCK = threading.RLock()
_CORPUS_DOCS_CACHE: Optional[List[str]] = None
_TERM_IDF_CACHE: Dict[str, float] = {}
MAX_TERM_IDF_CACHE = 4096
_AUTHOR_LEXICON_CACHE: Optional[List[str]] = None


def ensure_corpus_idf_cache() -> List[str]:
    """Lazily load compact lowercase document blobs once (~12ms) for dynamic BM25 IDF calculation."""
    global _CORPUS_DOCS_CACHE
    with _CACHE_LOCK:
        if _CORPUS_DOCS_CACHE is not None:
            return _CORPUS_DOCS_CACHE
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT title, subtitle, category, tags, substr(summary, 1, 180) AS s_head FROM books WHERE is_deleted = 0"
            ).fetchall()
            _CORPUS_DOCS_CACHE = [
                f"{r['title'] or ''} {r['subtitle'] or ''} {r['category'] or ''} {r['tags'] or ''} {r['s_head'] or ''}".lower()
                for r in rows
            ]
            return _CORPUS_DOCS_CACHE
        except Exception:
            _CORPUS_DOCS_CACHE = []
            return _CORPUS_DOCS_CACHE
        finally:
            conn.close()


# Alias for internal use
_ensure_corpus_idf_cache = ensure_corpus_idf_cache


def reset_corpus_caches() -> None:
    """Drop corpus-derived caches after books are added or edited; they rebuild lazily on the next query."""
    global _CORPUS_DOCS_CACHE, _AUTHOR_LEXICON_CACHE
    with _CACHE_LOCK:
        _CORPUS_DOCS_CACHE = None
        _AUTHOR_LEXICON_CACHE = None
        _TERM_IDF_CACHE.clear()


def _drop_common_short_names(names: Set[str], docs: List[str]) -> Set[str]:
    """Two-character names found in more than 30 books are ordinary words, not authors. One pass over the books'
    character pairs instead of a scan per name (7k names x 21k books took 11 s after every reindex)."""
    short = {n for n in names if len(n) <= 2}
    book_counts: Counter = Counter()
    for doc in docs:
        book_counts.update({doc[i:i + 2] for i in range(len(doc) - 1)} & short)
    return {n for n in names if len(n) > 2 or book_counts[n] <= 30}


def ensure_author_lexicon() -> List[str]:
    """Load distinct CJK author names (longest first) so queries like '刘慈欣科幻' keep the author as an anchor."""
    global _AUTHOR_LEXICON_CACHE
    with _CACHE_LOCK:
        if _AUTHOR_LEXICON_CACHE is not None:
            return _AUTHOR_LEXICON_CACHE
        conn = get_conn()
        try:
            names: Set[str] = set()
            for r in conn.execute("SELECT DISTINCT author FROM books WHERE is_deleted = 0"):
                for part in re.split(r"[/、,，;；&]+", r["author"] or ""):
                    part = re.sub(r"[\[\(（【][^\]\)）】]*[\]\)）】]", "", part)
                    part = re.sub(r"(?:等|著|编著|主编|编|译)$", "", part.strip()).strip()
                    if re.fullmatch(r"[一-鿿]{2,5}", part) and part not in DOMAIN_SYNONYM_MAP:
                        names.add(part)
                    segs = re.split(r"[·•．・.]", part)
                    if len(segs) >= 2:
                        surname = segs[-1].strip()
                        if re.fullmatch(r"[一-鿿]{2,5}", surname) and surname not in DOMAIN_SYNONYM_MAP:
                            names.add(surname)
            names.discard("未知作者")
            names = _drop_common_short_names(names, ensure_corpus_idf_cache())
            _AUTHOR_LEXICON_CACHE = sorted(names, key=len, reverse=True)
        except Exception:
            _AUTHOR_LEXICON_CACHE = []
        finally:
            conn.close()
        return _AUTHOR_LEXICON_CACHE


# Alias for internal use
_ensure_author_lexicon = ensure_author_lexicon


def get_corpus_idf_mult(term: str) -> float:
    """
    Compute dynamic Corpus Inverse Document Frequency (IDF) multiplier in [0.45, 1.45].
    Rare, specific domain concepts (e.g. '高敏感', '非理性决策', '芯片') get ~1.25..1.45x;
    broad frequent words (e.g. '心理', '管理', '经济') get ~0.55..0.72x.
    """
    t = (term or "").strip().lower()
    if not t:
        return 0.5
    with _CACHE_LOCK:
        if t in _TERM_IDF_CACHE:
            return _TERM_IDF_CACHE[t]
    docs = ensure_corpus_idf_cache()
    n_docs = len(docs)
    if n_docs == 0:
        return 1.0
    df = sum(1 for d in docs if t in d)
    raw_idf = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
    mult = round(min(1.45, max(0.45, 0.32 + raw_idf * 0.185)), 3)
    with _CACHE_LOCK:
        if len(_TERM_IDF_CACHE) >= MAX_TERM_IDF_CACHE:
            _TERM_IDF_CACHE.clear()
        _TERM_IDF_CACHE[t] = mult
    return mult
