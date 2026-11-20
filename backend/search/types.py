from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass(frozen=True)
class ShelfFilters:
    """Immutable filter facet constraints for shelf browsing & retrieval."""
    category: str = ""
    language: str = ""
    fmt: str = ""
    source_dir: str = ""
    reading_status: str = ""
    only_favorite: bool = False
    pub_period: str = ""
    has_rating: bool = False
    # One person from an author field (search.authors); matches books whose author field names them
    author: str = ""


@dataclass(frozen=True)
class ShelfQuery:
    """Full parameter object for shelf querying, pagination and sorting."""
    q: str = ""
    filters: ShelfFilters = field(default_factory=ShelfFilters)
    sort_by: str = "relevance"
    page: int = 1
    page_size: int = 24
