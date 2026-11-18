import abc
import re
import ssl
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class EnrichedBookMeta:
    """Standardized metadata structure returned by external book channel enrichers."""

    source: str
    external_id: str = ""
    rating: float = 0.0  # Normalized to 10.0 scale
    raw_rating: float = 0.0  # Original platform scale (e.g. 5.0)
    votes: int = 0
    description: str = ""
    reviews: List[Dict[str, Any]] = field(default_factory=list)
    cover_url: str = ""
    status: str = "success"  # success, not_found, error

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


_BILINGUAL_TITLE = re.compile(r"^[^()]*[\u4e00-\u9fff][^()]*\s\((.*[A-Za-z]{2}.*)\)\s*$")
_UNKNOWN_AUTHORS = {"", "未知作者", "unknown", "佚名"}


def search_title(title: str) -> str:
    """Title to look a book up by: the English original of a '中文 (English)' title, since the Chinese part
    is often a translation made up locally that matches unrelated Chinese books ('领导有方')."""
    m = _BILINGUAL_TITLE.match(title or "")
    return m.group(1).strip() if m else (title or "").strip()


def _norm(text: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (text or "").lower())


def titles_match(candidate: str, ours: str) -> bool:
    """Same book by title: equal after normalising, or one is the other plus a subtitle."""
    a, b = _norm(candidate), _norm(ours)
    if len(a) < 2 or len(b) < 2:
        return False
    return a == b or a.startswith(b) or b.startswith(a)


def authors_match(candidate: str, ours: str) -> bool:
    """True unless both authors are known and share nothing ('Davis, Paula' vs '余世维')."""
    if _norm(candidate) in _UNKNOWN_AUTHORS or _norm(ours) in _UNKNOWN_AUTHORS:
        return True
    a, b = _norm(candidate), _norm(ours)
    if a in b or b in a:
        return True
    words = lambda s: {w for w in re.findall(r"[a-z]{3,}", (s or "").lower())}
    # Two shared Chinese characters: '劉慈欣' (traditional, as Douban often lists it) is still '刘慈欣'
    hanzi = lambda s: set(re.findall(r"[\u4e00-\u9fff]", s or ""))
    return bool(words(candidate) & words(ours)) or len(hanzi(candidate) & hanzi(ours)) >= 2


@dataclass
class BookQuery:
    """Encapsulates search parameters for external channel lookups."""

    isbn: str = ""
    title: str = ""
    author: str = ""
    lang: str = "zh-CN"


class BaseBookEnricher(abc.ABC):
    """Abstract interface defining contract for external channel enrichers."""

    source_name: str = "base"

    def __init__(self, timeout_sec: float = 6.0) -> None:
        self.timeout_sec = timeout_sec

    def _get_ssl_context(self) -> Optional[ssl.SSLContext]:
        try:
            import certifi
            return ssl.create_default_context(cafile=certifi.where())
        except Exception:
            try:
                return ssl.create_default_context()
            except Exception:
                return ssl._create_unverified_context()

    @abc.abstractmethod
    def fetch(self, query: BookQuery) -> Optional[EnrichedBookMeta]:
        """Query the external channel and return standardized EnrichedBookMeta."""
        pass
