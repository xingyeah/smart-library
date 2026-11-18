"""
Are two files of one book record the same work? Rules of 同书归并与误合并拆分方案.md, section 3.

Signals in order of trust: content hash, ISBN, then titles (the title stored inside the file and the one in the
file name) with authors. A verdict is SAME, DIFFERENT or UNCERTAIN; uncertain pairs are for the user to decide,
never split or merged automatically. Reads nothing from disk: every input comes from the book_files row.
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Set

from ebook_parser.filenames import clean_spam_text, english_title_from_path, parse_filename_metadata
from enrichers.base import authors_match, search_title

SAME, DIFFERENT, UNCERTAIN = "same", "different", "uncertain"

SHORT_CJK_TITLE_CHARS = 3  # '原则', '思考': too common to merge on title alone without both authors
SHORT_LATIN_TITLE_WORDS = 1
MIN_SUBTITLE_PREFIX_CHARS = 4  # 'C++: …', 'AI: …' keep their whole title
MIN_TITLE_CHARS = 2
_UNKNOWN_AUTHORS = {"", "未知", "未知作者", "佚名", "unknown", "anonymous"}
_PLACEHOLDER_TITLES = {"未知", "未知书名", "untitled", "unknown", "无标题"}
_PLACEHOLDER_PREFIX = "microsoft word -"
_FILE_ARTIFACT = re.compile(r"::|\.(?:docx?|pdf|indd|qxd|tex|dvi|rtf|pptx?)\b", re.I)  # '::ODMAPCDOCS…', 'x.doc'
_CATALOGUE_NUMBER = re.compile(r"^[A-Za-z]{0,4}[0-9][0-9A-Za-z_\-.]*$")  # 'BL01x01', '0042'
_SUBTITLE_SEP = re.compile(r"[:：]|_\s")  # file names cannot hold ':', so 'Title_ Subtitle' stands for it
_LEADING_ARTICLE = re.compile(r"^(?:the|a|an)\s+")
_BRACKETED = re.compile(r"[(（\[【][^()（）\[\]【】]*[)）\]】]")
_SPACE_BY_CJK = re.compile(r"(?<=[一-鿿]) | (?=[一-鿿])")
MIN_TRUNCATED_PREFIX_WORDS = 4  # file names cut mid-word ('… in the Pacifi') still name the work when this long
MIN_CJK_PREFIX_CHARS = 6  # '追寻记忆的痕迹' covers '追寻记忆的痕迹-文字版'; '数学分析' must not cover '数学分析习题集'
_LEADING_BRACKET = re.compile(r"^[\[【(（]([^\]】)）]+)[\]】)）]")
_SITE_NAME = re.compile(r"^(?:www\.)?[a-z0-9-]+\.(?:com|net|org|cn|ru|info|io|me|cc|to)$")  # download-site watermark
_CJK = re.compile(r"[一-鿿]")
_LATIN = re.compile(r"[a-zA-Z]")


@dataclass
class FileFacts:
    """What one book_files row says about its work; titles are canonical and never placeholders."""

    file_id: int
    titles: Set[str]
    authors: List[str]
    isbn: str
    file_hash: str
    size: int


def canonical_title(text: str) -> str:
    """Lower case, subtitle and leading article dropped, '.'/'_' as spaces, hyphens joined, only letters and digits."""
    title = clean_spam_text(text or "").strip().lower()
    sep = _SUBTITLE_SEP.search(title)
    prefix = title[:sep.start()].strip() if sep else ""
    # '2001: A Space Odyssey' is the whole title, not a short prefix plus subtitle
    if sep and len(prefix) >= MIN_SUBTITLE_PREFIX_CHARS and not prefix.isdigit():
        title = prefix
    # '昆虫记(全译美绘本)', '草原帝国 (汉译世界学术名著丛书)': edition and series notes, unless they are the whole title
    lead = _LEADING_BRACKET.match(title)  # '[概率论沉思录]原版': a bracket at the start holds the title itself
    head, rest = (lead.group(1), title[lead.end():]) if lead else ("", title)
    title = f"{head} {_BRACKETED.sub(' ', rest)}".strip() or title
    title = re.sub(r"['’]", "", title)  # "Mao's" is 'Maos'
    title = re.sub(r"[._]", " ", title).replace("-", "")
    title = re.sub(r"[^0-9a-z一-鿿]+", " ", title).strip()
    title = _SPACE_BY_CJK.sub("", title)  # 'Go 语言实战' is 'Go语言实战'
    return _LEADING_ARTICLE.sub("", title)


def is_placeholder_title(text: str) -> bool:
    """Titles that name no work: 'Microsoft Word - x.doc', catalogue numbers, 'untitled', one character."""
    raw = (text or "").strip()
    low = raw.lower()
    if low.startswith(_PLACEHOLDER_PREFIX) or low in _PLACEHOLDER_TITLES or _CATALOGUE_NUMBER.match(raw) or _SITE_NAME.match(low)\
            or _FILE_ARTIFACT.search(raw):
        return True
    return len(canonical_title(raw).replace(" ", "")) < MIN_TITLE_CHARS


def normalize_isbn(text: str) -> str:
    """First ISBN in the text as ISBN-13 digits; '' when there is none."""
    digits = re.sub(r"[^0-9Xx]", "", text or "").upper()
    if len(digits) >= 13 and digits[:3] in ("978", "979"):
        return digits[:13]
    if len(digits) < 10 or not digits[:9].isdigit():
        return ""
    core = "978" + digits[:9]
    check = (10 - sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(core)) % 10) % 10
    return core + str(check)


def file_facts(row: Mapping[str, Any]) -> FileFacts:
    """Facts of one book_files row (needs id, original_path, archived_path, file_hash, file_size, isbn,
    embedded_title, embedded_author)."""
    path = row["original_path"] or row["archived_path"] or ""
    name_meta = parse_filename_metadata(path) if path else {}
    english = english_title_from_path(path) if path else ""
    name_author = name_meta.get("author") or ""
    if english and not _LATIN.search(name_author):
        # An English file name's Chinese bracket is the made-up name appended by a batch rename, not an author
        name_author = ""
    name_titles = [english] if english else [name_meta.get("title") or "", search_title(name_meta.get("title") or "")]
    raw_titles = [row["embedded_title"], *name_titles]
    authors = [a for a in (row["embedded_author"], name_author) if _is_known_author(a)]
    return FileFacts(
        file_id=row["id"],
        titles={canonical_title(t) for t in raw_titles if t and not is_placeholder_title(t)},
        authors=authors,
        isbn=normalize_isbn(row["isbn"]),
        file_hash=row["file_hash"] or "",
        size=row["file_size"] or 0,
    )



def import_item_facts(item: Mapping[str, Any]) -> FileFacts:
    """Facts of a file being imported (a parse_ebook result), before it has a book_files row."""
    return file_facts({
        "id": 0, "original_path": item["file_path"], "archived_path": "", "file_hash": item.get("file_hash") or "",
        "file_size": item.get("file_size") or 0, "isbn": item.get("isbn") or "",
        "embedded_title": item.get("embedded_title") or "", "embedded_author": item.get("embedded_author") or "",
    })

def _is_known_author(author: str) -> bool:
    name = (author or "").strip().lower()
    return name not in _UNKNOWN_AUTHORS and not _SITE_NAME.match(name)


def _script(texts) -> str:
    joined = " ".join(texts)
    has_cjk, has_latin = bool(_CJK.search(joined)), bool(_LATIN.search(joined))
    return "mixed" if has_cjk == has_latin else ("cjk" if has_cjk else "latin")


def _scripts_differ(a, b) -> bool:
    """'Daniel Kahneman' vs '丹尼尔·卡尼曼': nothing here can tell whether they name the same person or work."""
    sa, sb = _script(a), _script(b)
    return "mixed" not in (sa, sb) and sa != sb


def _is_short_title(title: str) -> bool:
    if _CJK.search(title):
        return len(title.replace(" ", "")) <= SHORT_CJK_TITLE_CHARS
    return len(title.split()) <= SHORT_LATIN_TITLE_WORDS


def _authors_agree(a: FileFacts, b: FileFacts) -> bool:
    return bool(a.authors and b.authors) and any(authors_match(x, y) for x in a.authors for y in b.authors)


def _judge_same_title(a: FileFacts, b: FileFacts, shared: Set[str]) -> str:
    if a.authors and b.authors:
        if _authors_agree(a, b):
            return SAME
        return UNCERTAIN if _scripts_differ(a.authors, b.authors) else DIFFERENT
    return UNCERTAIN if all(_is_short_title(t) for t in shared) else SAME


def _title_covers(short: str, long: str) -> bool:
    """'topology' covers 'topology 2 e'; a long enough title also covers a name cut mid-word or behind a prefix."""
    if short == long or long.startswith(short + " "):
        return True
    if len(short.split()) >= MIN_TRUNCATED_PREFIX_WORDS and (long.startswith(short) or long.endswith(" " + short)):
        return True  # '… in the Pacifi', 'ebooksclub org Phantoms in the Brain …'
    return len(_CJK.findall(short)) >= MIN_CJK_PREFIX_CHARS and long.startswith(short)


def _shared_titles(a: Set[str], b: Set[str]) -> Set[str]:
    """Titles the two files share; of a pair where one is a prefix of the other, the shorter one."""
    shared = set()
    for x in a:
        for y in b:
            short, long = sorted((x, y), key=len)
            if _title_covers(short, long):
                shared.add(short)
    return shared


def judge_same_work(a: FileFacts, b: FileFacts) -> str:
    """SAME, DIFFERENT or UNCERTAIN for two files; the first rule that decides wins.

    Different ISBNs decide nothing: they mark another edition of one work just as often as another work."""
    if a.file_hash and a.file_hash == b.file_hash:
        return SAME
    if a.isbn and a.isbn == b.isbn:
        return SAME
    if not a.titles or not b.titles:
        return UNCERTAIN  # placeholder or catalogue-number names: only hash or ISBN may join them
    shared = _shared_titles(a.titles, b.titles)
    if shared:
        return _judge_same_title(a, b, shared)
    if not _scripts_differ(a.titles, b.titles):
        return DIFFERENT
    # '清醒思考的艺术' and 'The Art of Thinking Clearly' by the same author, inside one record: one work
    return SAME if _authors_agree(a, b) else UNCERTAIN


def plan_book_split(rows: List[Mapping[str, Any]], book_title: str) -> Dict[str, Any]:
    """Which files of one record stay with it, which belong to another work, which the user must decide.

    The anchor is a file whose title is the record's (the book the user saw, and whose favourites and progress
    stay on this record), then the one most others agree with (not the primary: it may be the stray file),
    then one with an ISBN, then the larger file."""
    facts = [file_facts(r) for r in rows]
    verdicts = {(i, j): judge_same_work(facts[i], facts[j]) for i in range(len(facts)) for j in range(len(facts)) if i < j}
    agree = [sum(1 for pair, v in verdicts.items() if v == SAME and i in pair) for i in range(len(facts))]
    own_title = canonical_title(search_title(book_title))
    anchor = max(range(len(facts)), key=lambda i: (bool(_shared_titles({own_title}, facts[i].titles)), agree[i], bool(facts[i].isbn), facts[i].size))
    plan: Dict[str, Any] = {"anchor": facts[anchor].file_id, SAME: [], DIFFERENT: [], UNCERTAIN: []}
    for i, fact in enumerate(facts):
        if i != anchor:
            plan[verdicts[(min(i, anchor), max(i, anchor))]].append(fact.file_id)
    return plan
