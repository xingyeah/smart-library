"""Lexical points one query term earns in one book's fields (whole-word fields and the half-word fallback)."""

import re
from typing import Dict, List, Tuple

from search.lexicon import (
    AMBIGUOUS_SHORT_DOMAIN_KEYS,
    CONVERSATIONAL_STOPWORDS,
    DOMAIN_SYNONYM_MAP,
    TIER2_CONTEXT_MODIFIERS,
)

# Whole-word fields that can stack under the field that scored the term, highest first. AI core points and
# target audience are left out: scoring them at 28 cost gate nDCG@10 −0.007 (shelf) in the Phase 1 A/B.
STACKABLE_FIELD_PTS = (("meta", 44.0), ("summary", 36.0), ("ai_keywords", 30.0), ("toc", 22.0))
FIELD_PTS = dict(STACKABLE_FIELD_PTS)
# A 2-char half of a compound ('科幻' of '科幻史诗') scores below every whole-word hit (lowest: table of contents,
# 22), so books that only share half the word never outrank the books that contain all of it.
HALF_WORD_TITLE_PTS = 16.0
HALF_WORD_TEXT_PTS = 9.0
# Both halves present ('科幻' and '史诗') is stronger evidence than one, still below a whole-word title hit.
BOTH_HALVES_TITLE_PTS = 30.0
BOTH_HALVES_TEXT_PTS = 23.0
# A whole word also found in lower-ranked fields adds a quarter of their points, capped at 30% of the best field.
SECONDARY_FIELD_SHARE = 0.25
SECONDARY_FIELD_CAP = 0.30
_CJK_WORD = re.compile(r"[一-鿿]+")


def secondary_field_bonus(main_field: str, main_pts: float, field_hits: Dict[str, bool]) -> float:
    """Extra points for a whole word that also appears in fields ranked below the one that scored it."""
    names = [name for name, _ in STACKABLE_FIELD_PTS]
    start = names.index(main_field) + 1 if main_field in names else 0
    others = sum(pts for name, pts in STACKABLE_FIELD_PTS[start:] if field_hits.get(name))
    return min(SECONDARY_FIELD_CAP * main_pts, SECONDARY_FIELD_SHARE * others)


def meaningful_halves(term: str) -> List[str]:
    """The 2-char halves of a 3–4 char Chinese compound that mean something alone ('投资方法' → ['投资']).

    English words are left out: 'ru' and 'st' of 'rust' are letters, not words. Domain terms have synonyms, and
    a half of '比特币' ('比特', bit) is a different word.
    """
    if len(term) not in (3, 4) or not _CJK_WORD.fullmatch(term) or term in DOMAIN_SYNONYM_MAP:
        return []
    halves = dict.fromkeys((term[:2], term[-2:]))
    generic = (AMBIGUOUS_SHORT_DOMAIN_KEYS, CONVERSATIONAL_STOPWORDS, TIER2_CONTEXT_MODIFIERS)
    return [h for h in halves if not any(h in words for words in generic)]


def half_word_hit(term: str, title_text: str, body_text: str) -> Tuple[float, str, bool]:
    """(points, matched half, found in title) for a term only its halves match; (0.0, '', False) when none does.

    A title hit on either half wins over a body hit, so a blurb match on the first half never hides a title
    match on the second.
    """
    halves = meaningful_halves(term)
    has_both = len(halves) == 2 and all(h in title_text or h in body_text for h in halves)
    for half in halves:
        if half in title_text:
            return (BOTH_HALVES_TITLE_PTS if has_both else HALF_WORD_TITLE_PTS), half, True
    for half in halves:
        if half in body_text:
            return (BOTH_HALVES_TEXT_PTS if has_both else HALF_WORD_TEXT_PTS), half, False
    return 0.0, "", False
