"""Author names inside a book's author field: split for display, keyed for 'all books by this author' matching."""

import functools
import re
from typing import Any, Dict, FrozenSet, List

from search.hanzi import to_simplified

# Between people. '·' '・' '．' are kept: they sit inside one name (丹尼尔·卡尼曼).
_PEOPLE_SEPARATOR = re.compile(r"\s*(?:、|/|／|&|＆|；|;|\band\b)\s*", re.I)
_COMMA = re.compile(r"\s*[,，]\s*")
# '[美]', '（英）' before a name; '(Editor)', '著', '编', '等', 'et al.' after it
_LEADING_TAG = re.compile(r"^\s*[\[［(（【〔][^\]］)）】〕]{1,8}[\]］)）】〕]\s*")
_TRAILING_ROLE = re.compile(r"\s*(?:[(（][^)）]*[)）]|编著|主编|著|编|译|等|et\s+al\.?|etc\.?)\s*$", re.I)
_TITLE_ONLY = re.compile(r"^(?:m\.?d\.?|ph\.?d\.?|jr\.?|sr\.?|dr\.?|prof\.?)$", re.I)
_LETTERS = re.compile(r"[A-Za-z\u00C0-\u024F]{2}|[\u4e00-\u9fff\u3400-\u4dbf]")
_GARBLE = re.compile(r"[<>=@{}|\\^~`]")
_WORD = re.compile(r"\w+")
UNKNOWN_AUTHORS = frozenset({"未知作者", "佚名", "unknown", "anonymous"})
AUTHOR_MATCH_SQL_FUNCTION = "author_matches"
# Longer input to the author filter is not one person's name; the cap bounds the key work per request
MAX_AUTHOR_NAME_CHARS = 120


def _clean_name(part: str) -> str:
    name = _LEADING_TAG.sub("", part)
    previous = None
    while previous != name:
        previous, name = name, _TRAILING_ROLE.sub("", name)
    return name.strip(" .，,")


def _split_commas(chunk: str) -> List[str]:
    parts = _COMMA.split(chunk)
    # 'Efron, Bradley' is one person written surname first; 'Martin Osborne, Ariel Rubinstein' is two
    if len(parts) == 2 and " " not in parts[0] and parts[0].isascii() and parts[0].isalpha():
        return [chunk]
    return parts


def split_authors(author: str) -> List[str]:
    """The people named in an author field, in order, without nationality tags, roles or duplicates."""
    names: List[str] = []
    for chunk in _PEOPLE_SEPARATOR.split(author or ""):
        for part in _split_commas(chunk):
            name = _clean_name(part)
            if name and not _TITLE_ONLY.match(name) and name not in names:
                names.append(name)
    return names


def author_key(name: str) -> str:
    """Spelling-insensitive key: case, spacing, punctuation, script and word order ('Efron, Bradley') ignored."""
    return "".join(sorted(_WORD.findall(to_simplified(name).lower())))


def is_linkable_author(name: str) -> bool:
    """False for placeholders ('未知作者') and garbled names ('4<8=8AB@0B>@') that would list unrelated books."""
    return (
        name.lower() not in UNKNOWN_AUTHORS
        and not _GARBLE.search(name)
        and bool(_LETTERS.search(name))
        and bool(author_key(name))
    )


def author_links(author: str) -> List[Dict[str, Any]]:
    """[{name, linkable}] for the book detail's author line."""
    return [{"name": name, "linkable": is_linkable_author(name)} for name in split_authors(author)]


@functools.lru_cache(maxsize=32768)
def _author_field_keys(author: str) -> FrozenSet[str]:
    return frozenset(author_key(name) for name in split_authors(author))


def _author_matches(author: Any, key: str) -> int:
    return int(bool(author) and key in _author_field_keys(str(author)))


def register_author_sql(conn: Any) -> None:
    """Adds author_matches(author_field, author_key) to `conn`, used by the shelf's author filter."""
    conn.create_function(AUTHOR_MATCH_SQL_FUNCTION, 2, _author_matches, deterministic=True)
