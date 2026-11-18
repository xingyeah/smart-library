import logging
import math
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from ebook_parser import build_cjk_search_tokens
from enricher import CATEGORY_RULES
from native_bridge import get_native_bridge
from search.corpus_stats import (
    ensure_author_lexicon as _ensure_author_lexicon,
    ensure_corpus_idf_cache as _ensure_corpus_idf_cache,
    get_corpus_idf_mult,
)
from search.lexicon import (
    ADVANCED_LEVEL_MARKERS,
    AMBIGUOUS_SHORT_DOMAIN_KEYS,
    ANTHOLOGY_MODIFIERS,
    BROAD_PARENT_NOUNS,
    CLASSIC_INTENT_MODIFIERS,
    CONVERSATIONAL_STOPWORDS,
    DOMAIN_SYNONYM_MAP,
    ENGLISH_SENTENCE_MIN_WORDS,
    ENGLISH_STOPWORDS,
    HARDCORE_STYLE_MODIFIERS,
    INTRO_LEVEL_MARKERS,
    KNOWN_ENGLISH_TECH_TERMS,
    NON_TECH_MARKERS,
    POPSCI_COMIC_MARKERS,
    POPSCI_STYLE_MODIFIERS,
    TARGET_GROUP_MODIFIERS,
    TECH_DEPTH_MARKERS,
    TECH_DEPTH_MODIFIERS,
    TEXTBOOK_POSITIVE_MARKERS,
    TIER2_CONTEXT_MODIFIERS,
    _ENGLISH_WORD,
    _SHORT_ASCII_TERM,
    _STRIP_PREFS,
    _STRIP_SUFS,
    _CONVERSATIONAL_FRAME_REGEX,
    _has_term,
)


def _detect_pinyin_query(q_clean: str) -> Tuple[bool, str, List[str]]:
    """
    Detect if a query is a Chinese Pinyin search (e.g. 'santi', 'sikao kuai yu man', 'lcx').
    Returns (is_pinyin, joined_pinyin, pinyin_parts).
    """
    if not re.match(r"^[a-zA-Z\s]{2,45}$", q_clean):
        return False, "", []
    parts = [p.lower() for p in q_clean.split() if p.strip()]
    if not parts:
        return False, "", []
    # If any part is a known English tech/domain word or English filler, treat as English keyword query
    if any(p in KNOWN_ENGLISH_TECH_TERMS or p in ENGLISH_STOPWORDS for p in parts):
        return False, "", []
    joined = "".join(parts)
    if len(joined) < 2:
        return False, "", []
    return True, joined, parts


def analyze_query_intent(raw_query: str) -> Dict[str, Any]:
    """
    4-Layer Natural Language Understanding (NLU) Query Analyzer:
      1. Negative Constraint & Style Override Extraction ('不要鸡汤', '不要太枯燥')
      2. Pinyin & Initials Detection ('santi', 'sikao kuai yu man')
      3. Multi-Char Conjunction Normalization & Greedy Span-Locked Ontology Extraction
      4. Syntactic Frame Stripping & NLTokenizer Atomic Word Extraction
    """
    q = (raw_query or "").strip()
    m_book = re.search(r"[《“\"'「【]([^》”\"'」】]+)[》”\"'」】]", q)
    explicit_title = m_book.group(1).strip() if m_book else ""
    is_quoted_title = bool(explicit_title)
    q_clean = re.sub(r"[《》“”\"'「」【】]+", " ", q).strip()
    if not explicit_title and 2 <= len(q_clean) <= 15 and not any(w in q_clean.lower() for w in CONVERSATIONAL_STOPWORDS):
        explicit_title = q_clean
    q_low = q_clean.lower()

    wants_classic = any(m in q_low for m in CLASSIC_INTENT_MODIFIERS)
    wants_anthology = any(m in q_low for m in ANTHOLOGY_MODIFIERS)

    if any(m in q_low for m in HARDCORE_STYLE_MODIFIERS):
        style_mode = "hardcore_textbook"
    elif any(m in q_low for m in POPSCI_STYLE_MODIFIERS):
        style_mode = "intro_popsci"
    elif any(m in q_low for m in TECH_DEPTH_MODIFIERS):
        style_mode = "technical_depth"
    else:
        style_mode = "neutral"

    # Layer 1: Extract Negative Clauses ('不要鸡汤', '不要太枯燥的', '不看小说')
    negative_terms: List[str] = []
    raw_clauses = re.split(r"[,，、；;。！？!?\n]+", q_low)
    positive_clauses: List[str] = []

    for cl in raw_clauses:
        cl_s = cl.strip()
        if not cl_s:
            continue
        m_neg = re.search(r"(?:不要|不看|别推|别给|拒绝|不想要|排斥)\s*(.+)$", cl_s)
        if m_neg:
            neg_body = m_neg.group(1).strip()
            # Keep any part before the negation if present
            prefix_part = cl_s[: m_neg.start()].strip()
            if prefix_part:
                positive_clauses.append(prefix_part)

            if any(w in neg_body for w in ("枯燥", "晦涩", "深奥", "太难", "学术", "大部头")):
                style_mode = "intro_popsci"
            elif any(w in neg_body for w in ("鸡汤", "成功学", "心灵励志", "太浅", "水书", "快餐")):
                if style_mode == "neutral":
                    style_mode = "hardcore_textbook"
                for nt in ("鸡汤", "心灵鸡汤", "成功学", "励志"):
                    if nt not in negative_terms:
                        negative_terms.append(nt)
            else:
                neg_clean = re.sub(r"^(?:太|过于|那种|的)+|(?:的书|的书籍|的|了)+$", "", neg_body).strip()
                if 2 <= len(neg_clean) <= 6 and neg_clean not in negative_terms:
                    negative_terms.append(neg_clean)
        else:
            positive_clauses.append(cl_s)

    pos_text = " ， ".join(positive_clauses) if positive_clauses else q_low

    # Layer 2: Check for pure Pinyin query ('santi', 'sikao kuai yu man')
    is_pinyin, pinyin_joined, pinyin_parts = _detect_pinyin_query(pos_text)
    if is_pinyin:
        py_syns = pinyin_parts if len(pinyin_parts) > 1 else []
        return {
            "raw_query": q,
            "primary_terms": [pinyin_joined],
            "core_terms": [pinyin_joined],
            "parent_nouns": [],
            "synonym_terms": py_syns,
            "expanded_terms": py_syns,
            "negative_terms": negative_terms,
            "is_pinyin": True,
            "pinyin_joined": pinyin_joined,
            "inferred_categories": [],
            "wants_classic": wants_classic,
            "wants_anthology": wants_anthology,
            "style_mode": style_mode,
            "needs_reranker": False,
            "semantic_query": q,
        }

    core_terms: List[str] = []
    synonym_terms: List[str] = []

    # Layer 3: Greedy Longest-Match Domain Ontology Extraction with Character Span Locking
    # Prevents "精力管理" from falsely triggering ambiguous short key "管理" -> Business Management
    consumed = [False] * len(pos_text)
    for dom_k in sorted(DOMAIN_SYNONYM_MAP.keys(), key=len, reverse=True):
        start_idx = 0
        while True:
            idx = pos_text.find(dom_k, start_idx)
            if idx == -1:
                break
            end_idx = idx + len(dom_k)
            # 'ai' inside 'email' / 'brain' is not the AI concept
            if _SHORT_ASCII_TERM.fullmatch(dom_k) and (
                (idx > 0 and pos_text[idx - 1].isascii() and pos_text[idx - 1].isalnum())
                or (end_idx < len(pos_text) and pos_text[end_idx].isascii() and pos_text[end_idx].isalnum())
            ):
                start_idx = idx + 1
                continue
            # If this is an ambiguous short key (like '管理'), skip if any char in its span is already consumed
            if dom_k in AMBIGUOUS_SHORT_DOMAIN_KEYS and any(consumed[idx:end_idx]):
                start_idx = idx + 1
                continue
            # Also skip if completely inside an already consumed compound span
            if all(consumed[idx:end_idx]):
                start_idx = idx + 1
                continue
            for c_i in range(idx, end_idx):
                consumed[c_i] = True
            if dom_k not in core_terms:
                core_terms.append(dom_k)
            for syn in DOMAIN_SYNONYM_MAP[dom_k]:
                if syn not in synonym_terms:
                    synonym_terms.append(syn)
            start_idx = end_idx

    # Layer 3B: Known author names embedded in the query ('刘慈欣科幻' -> '刘慈欣')
    author_terms: List[str] = []
    for author_name in _ensure_author_lexicon():
        idx = pos_text.find(author_name)
        if idx != -1 and not all(consumed[idx : idx + len(author_name)]):
            for c_i in range(idx, idx + len(author_name)):
                consumed[c_i] = True
            if author_name not in core_terms:
                core_terms.append(author_name)
            author_terms.append(author_name)

    # Domain keys and author names found so far; chunks are split around them so '斯多葛哲学' keeps '斯多葛'
    anchor_terms = sorted((t for t in core_terms if len(t) >= 2), key=len, reverse=True)
    anchor_split_re = re.compile("|".join(map(re.escape, anchor_terms))) if anchor_terms else None

    # Layer 4: Multi-Char Conjunction Normalization & Syntactic Frame Stripping
    norm_text = re.sub(r"(?:或者|或是|还是|以及|并且|同时|还有|外加|加上)", "、", pos_text)
    sub_clauses = re.split(r"[,，、；;。！？!?\s/和与或及]+", norm_text)

    for cl in sub_clauses:
        s = cl.strip()
        if not s:
            continue
        # Extract any embedded English/tech identifiers (e.g. 'python', 'c++', 'llm', 'ai')
        for en_m in re.findall(r"[a-zA-Z][a-zA-Z0-9\+\#\.]{1,18}", s):
            en_low = en_m.lower()
            if en_low not in CONVERSATIONAL_STOPWORDS:
                if en_low not in core_terms:
                    core_terms.append(en_low)
                if en_low in DOMAIN_SYNONYM_MAP:
                    for syn in DOMAIN_SYNONYM_MAP[en_low]:
                        if syn not in synonym_terms:
                            synonym_terms.append(syn)

        # Strip conversational prefixes/verbs and modifier suffixes
        s = _CONVERSATIONAL_FRAME_REGEX.sub(" ", s).strip()
        for sub_part in s.split():
            sp = sub_part.strip()
            for pref in _STRIP_PREFS:
                if sp.startswith(pref) and len(sp) > len(pref):
                    sp = sp[len(pref) :].strip()
            changed = True
            while changed and len(sp) >= 2:
                changed = False
                for suf in _STRIP_SUFS:
                    if sp.endswith(suf) and len(sp) > len(suf):
                        sp = sp[: -len(suf)].strip()
                        changed = True
                        break
            # Remove pure ASCII from CJK chunk if already extracted above
            sp_cjk = re.sub(r"[a-zA-Z0-9\+\#\.]+", "", sp).strip()
            residual_parts: List[str] = []
            # Only split chunks too long to keep whole ('斯多葛哲学'); '价值投资' is already a valid term
            splits_broad_noun = any(
                sp_cjk != t and (sp_cjk.startswith(t) or sp_cjk.endswith(t))
                for t in anchor_terms
                if t in TIER2_CONTEXT_MODIFIERS
            )  # '二战历史' -> '二战' + broad '历史'
            if anchor_split_re is not None and (len(sp_cjk) > 4 or splits_broad_noun) and sp_cjk not in DOMAIN_SYNONYM_MAP:
                for piece in anchor_split_re.split(sp_cjk):
                    piece = re.sub(r"[《》“”\"'「」【】]+", "", piece).strip().strip("的之")
                    for sw in CONVERSATIONAL_STOPWORDS:
                        if len(sw) >= 2 and piece.startswith(sw):
                            piece = piece[len(sw) :].strip("的之")
                    if len(piece) >= 2:
                        residual_parts.append(piece)
            for candidate in (sp, sp_cjk, *residual_parts):
                candidate = re.sub(r"[《》“”\"'「」【】]+", "", candidate).strip()
                # '赫拉利写' wraps an author already captured in Layer 3B
                if candidate not in author_terms and any(a in candidate for a in author_terms):
                    continue
                if (
                    (2 <= len(candidate) <= 4 or candidate in DOMAIN_SYNONYM_MAP)
                    and candidate not in CONVERSATIONAL_STOPWORDS
                    and candidate not in CLASSIC_INTENT_MODIFIERS
                    and candidate not in HARDCORE_STYLE_MODIFIERS
                    and candidate not in POPSCI_STYLE_MODIFIERS
                    and candidate not in ANTHOLOGY_MODIFIERS
                ):
                    if candidate not in core_terms:
                        core_terms.append(candidate)
                    if candidate in DOMAIN_SYNONYM_MAP:
                        for syn in DOMAIN_SYNONYM_MAP[candidate]:
                            if syn not in synonym_terms:
                                synonym_terms.append(syn)

    # Layer 5: Native NLTokenizer Word-Level Segmentation on Positive Text
    try:
        bridge = get_native_bridge()
        exp = bridge.expand_query(pos_text)
        for tok in exp.get("tokens") or []:
            t = tok.strip().lower()
            if (
                2 <= len(t) <= 6
                and t not in CONVERSATIONAL_STOPWORDS
                and t not in CLASSIC_INTENT_MODIFIERS
                and t not in HARDCORE_STYLE_MODIFIERS
                and t not in POPSCI_STYLE_MODIFIERS
                and t not in ANTHOLOGY_MODIFIERS
            ):
                # Skip sub-tokens that are already part of an extracted compound concept (e.g. '精力' or '管理' inside '精力管理')
                if any(t in ct and t != ct for ct in core_terms):
                    continue
                if t not in core_terms:
                    core_terms.append(t)
                if t in DOMAIN_SYNONYM_MAP:
                    for syn in DOMAIN_SYNONYM_MAP[t]:
                        if syn not in synonym_terms:
                            synonym_terms.append(syn)
    except Exception:
        # Fallback to character n-grams if macOS native NLTokenizer bridge is unavailable
        logging.getLogger(__name__).debug("native tokenizer unavailable; using character n-grams", exc_info=True)

    # '管理焦虑' / '管理情绪': 管理 is the verb, not business management
    if "管理" in core_terms and re.search(
        r"管理(?:好|自己的|你的|我的)?(?:焦虑|情绪|时间|精力|压力|体重|睡眠|注意力|健康|家庭|关系|自己)", pos_text
    ):
        core_terms.remove("管理")
        for syn in DOMAIN_SYNONYM_MAP.get("管理", []):
            if syn in synonym_terms:
                synonym_terms.remove(syn)

    # Fallback if user literally typed only "经典" or "教材"
    if not core_terms and q_low:
        cleaned_raw = re.sub(r"[^\w\u4e00-\u9fff]+", "", q_low)
        if cleaned_raw:
            core_terms.append(cleaned_raw)

    # A compound of two aspect/context words ('底层技术', '技术原理') is a modifier, not a topic: keep its parts
    split_terms: List[str] = []
    for t in core_terms:
        parts = next(
            ([t[:k], t[k:]] for k in range(2, len(t) - 1) if t[:k] in TIER2_CONTEXT_MODIFIERS and t[k:] in TIER2_CONTEXT_MODIFIERS),
            [t],
        )
        split_terms.extend(parts)
    core_terms = list(dict.fromkeys(split_terms))

    # Layer 6: 3-Tier Semantic Role Partitioning + Dynamic Corpus IDF Weighting
    tier1_terms = [t for t in core_terms if t not in TIER2_CONTEXT_MODIFIERS]
    tier2_terms = [t for t in core_terms if t in TIER2_CONTEXT_MODIFIERS]

    term_weights: Dict[str, float] = {}
    for t in tier1_terms:
        role_base = 1.22 if len(t) >= 3 else 1.0
        term_weights[t] = round(role_base * get_corpus_idf_mult(t), 3)
    for t in tier2_terms:
        term_weights[t] = round(0.52 * get_corpus_idf_mult(t), 3)

    # Only a quoted title becomes a term: an unquoted short query is usually a topic ('明朝历史人物')
    if is_quoted_title and len(explicit_title) >= 2:
        clean_et = re.sub(r"[《》“”\"'「」【】\s]+", "", explicit_title).lower()
        if clean_et and clean_et not in core_terms:
            core_terms.insert(0, clean_et)
        if clean_et and clean_et not in tier1_terms:
            tier1_terms.insert(0, clean_et)
        term_weights[clean_et] = 3.5

    # Sort Tier-1 terms by descending weight (highest specificity / IDF first)
    tier1_terms.sort(key=lambda x: term_weights.get(x, 1.0), reverse=True)
    tier2_terms.sort(key=lambda x: term_weights.get(x, 0.5), reverse=True)

    if tier1_terms:
        primary_terms = tier1_terms
        parent_nouns = tier2_terms
    else:
        primary_terms = tier2_terms
        parent_nouns = []

    # Infer target categories jointly from Tier-1 core anchors + Tier-2 domain context (e.g. '敏感' + '心理' -> 心理学与脑科学)
    all_topical = primary_terms + parent_nouns
    inferred_cats = infer_query_categories(" ".join(all_topical), all_topical, synonym_terms)

    # Construct clean semantic query for bge-m3 embedding (positive clauses + key extracted concepts)
    clean_pos_sentence = "，".join(positive_clauses) if positive_clauses else q
    sem_parts = [clean_pos_sentence]
    if primary_terms:
        sem_parts.append(" ".join(primary_terms[:6]))
    if parent_nouns:
        sem_parts.append(" ".join(parent_nouns[:3]))
    if synonym_terms:
        sem_parts.append(" ".join(synonym_terms[:4]))
    semantic_query = " ".join(sem_parts)

    # Run Cross-Encoder reranking on multi-concept or conversational natural language queries
    needs_reranker = (
        len(q) >= 8
        or len(primary_terms) >= 2
        or style_mode != "neutral"
        or any(ch in q for ch in " ，,、；;？?")
    )

    return {
        "raw_query": q,
        "primary_terms": primary_terms,
        "core_terms": primary_terms,
        "tier1_terms": tier1_terms,
        "tier2_terms": tier2_terms,
        "parent_nouns": parent_nouns,
        "term_weights": term_weights,
        "synonym_terms": synonym_terms,
        "expanded_terms": synonym_terms,
        "negative_terms": negative_terms,
        "is_pinyin": False,
        "pinyin_joined": "",
        "inferred_categories": inferred_cats,
        "wants_classic": wants_classic,
        "wants_anthology": wants_anthology,
        "style_mode": style_mode,
        "needs_reranker": needs_reranker,
        "semantic_query": semantic_query,
        "author_terms": author_terms,
        # 'Python或者数据分析': concepts are alternatives, so covering both earns no bonus
        "is_disjunctive": bool(re.search(r"或者|或是|还是|\bor\b", pos_text)),
        "explicit_title": explicit_title,
    }


def infer_query_categories(
    topic_text: str, primary_terms: List[str], synonym_terms: List[str]
) -> List[str]:
    """Infer target book categories strictly from topical terms."""
    combined = f"{topic_text} {' '.join(primary_terms)} {' '.join(synonym_terms[:8])}".lower()
    cat_scores: List[Tuple[str, int]] = []
    for cat, kws in CATEGORY_RULES:
        sc = 0
        if cat.lower() in combined:
            sc += 8
        for kw in kws:
            kw_low = kw.lower()
            if kw_low in topic_text.lower():
                sc += 5
            elif kw_low in combined:
                sc += 2
        if sc > 0:
            cat_scores.append((cat, sc))
    cat_scores.sort(key=lambda x: x[1], reverse=True)
    return [c for c, _ in cat_scores[:3]]


def build_fts_query(
    primary_terms: List[str], synonym_terms: Optional[List[str]] = None
) -> str:
    """
    Build SQLite FTS5 query strictly from topical terms (never from modifiers like '经典' or '书籍').
    Uses OR across topical terms & synonyms so BM25 naturally scores multi-term hits higher
    without zeroing out books that use synonymous terminology.
    """
    clean_primary: List[str] = []
    seen: Set[str] = set()
    for t in primary_terms:
        tc = re.sub(r'[^\w\u4e00-\u9fff]+', "", t).lower()
        if tc and tc not in seen:
            seen.add(tc)
            clean_primary.append(f'"{tc}"')

    clean_syns: List[str] = []
    if synonym_terms:
        for st in synonym_terms[:6]:
            stc = re.sub(r'[^\w\u4e00-\u9fff]+', "", st).lower()
            if len(stc) >= 2 and stc not in seen:
                seen.add(stc)
                clean_syns.append(f'"{stc}"')

    all_q = clean_primary + clean_syns
    if not all_q:
        return ""
    if len(clean_primary) >= 2:
        and_part = f"({' AND '.join(clean_primary[:3])})"
        or_part = f"({' OR '.join(all_q[:8])})"
        return f"{and_part} OR {or_part}"
    return " OR ".join(all_q[:8])


def _parse_language_filter(language: str) -> List[str]:
    if not language or language.strip() in ("", "全部", "all"):
        return []
    valid = {"zh-CN", "zh-TW", "en"}
    parts = [p.strip() for p in language.split(",") if p.strip() in valid]
    return [] if len(parts) >= 3 else parts


def _build_pub_period_clause(period: str) -> str:
    """Build SQL condition for decade / publication era filter."""
    m = {
        "2020s": "b.pub_year >= '2020'",
        "2010s": "b.pub_year >= '2010' AND b.pub_year < '2020'",
        "2000s": "b.pub_year >= '2000' AND b.pub_year < '2010'",
        "1900s": "b.pub_year >= '1900' AND b.pub_year < '2000'",
        "pre1900": "b.pub_year < '1900' AND length(trim(b.pub_year)) > 0",
    }
    return m.get(period or "", "")


_QUERY_TRANSLATION_CACHE: Dict[str, str] = {}
_QUERY_TRANSLATION_CACHE_MAX = 512


def _translate_english_query(query: str) -> str:
    """Chinese rendering of an English natural-language query (on-device Apple translation), '' when not applicable."""
    q = query.strip()
    if not re.fullmatch(r"[A-Za-z0-9\s,.'?!:&+#-]+", q) or len(q.split()) < ENGLISH_SENTENCE_MIN_WORDS:
        return ""
    if q in _QUERY_TRANSLATION_CACHE:
        return _QUERY_TRANSLATION_CACHE[q]
    from translator import _translate_batch_via_apple  # local import: translator is not needed for Chinese queries

    out = _translate_batch_via_apple([q], "en", "zh-CN")
    zh = out[0].strip() if out else ""
    if len(_QUERY_TRANSLATION_CACHE) >= _QUERY_TRANSLATION_CACHE_MAX:
        _QUERY_TRANSLATION_CACHE.clear()
    _QUERY_TRANSLATION_CACHE[q] = zh
    return zh



