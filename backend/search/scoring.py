import math
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from search.corpus_stats import get_corpus_idf_mult
from search.lexicon import (
    ADVANCED_LEVEL_MARKERS,
    ANTHOLOGY_MODIFIERS,
    BROAD_PARENT_NOUNS,
    CLASSIC_INTENT_MODIFIERS,
    DOMAIN_SYNONYM_MAP,
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
    _ENGLISH_WORD,
    _NON_TECH_KINDS,
    _SHORT_ASCII_TERM,
    _has_term,
)
from search.hanzi import to_simplified
from search.shelf import _format_book_row
from search.term_points import FIELD_PTS, half_word_hit, secondary_field_bonus

ANTHOLOGY_PENALTY = -12.0


def pre_score_candidates(
    raw_query: str,
    intent: Dict[str, Any],
    top_cand_ids: List[int],
    rows_by_id: Dict[int, Any],
    ai_meta: Dict[int, Dict[str, Any]],
    dense_scores: Dict[int, float],
    category: str,
    inferred_cats: List[str],
    category_relaxed: bool,
) -> List[Dict[str, Any]]:
    """
    Stage 4A: Pre-score candidates with multi-concept coverage + lexical + bge-m3 dense
    + classic_score + category prior.
    """
    primary_terms: List[str] = intent.get('primary_terms') or []
    synonym_terms: List[str] = intent.get('synonym_terms') or []
    term_weights: Dict[str, float] = intent.get('term_weights') or {}
    negative_terms: List[str] = intent.get('negative_terms') or []
    author_terms: Set[str] = set(intent.get('author_terms') or [])
    is_disjunctive: bool = bool(intent.get('is_disjunctive'))
    is_pinyin: bool = bool(intent.get('is_pinyin'))
    pinyin_joined: str = intent.get('pinyin_joined') or ''
    wants_classic: bool = bool(intent.get('wants_classic'))
    wants_anthology: bool = bool(intent.get('wants_anthology'))
    style_mode: str = intent.get('style_mode') or 'neutral'
    parent_nouns: List[str] = intent.get('parent_nouns') or []
    tier1_terms: List[str] = intent.get('tier1_terms') or []
    tier2_terms: List[str] = intent.get('tier2_terms') or []

    # Stage 4A: Pre-score candidates with multi-concept coverage + lexical + bge-m3 dense + classic_score + category prior
    prelim_pool: List[Dict[str, Any]] = []
    q_exact_low = raw_query.strip().lower()

    for bid in top_cand_ids:
        row = rows_by_id.get(bid)
        if row is None:
            continue
        pinyin_raw = (row["pinyin_search"] or "").lower()
        b = _format_book_row(row)

        title_low = (b.get("title") or "").lower()
        sub_low = (b.get("subtitle") or "").lower()
        title_sub = f"{title_low} {sub_low}"
        author_low = (b.get("author") or "").lower()
        meta = ai_meta.get(bid) or {}
        title_sub = f"{title_sub} {(meta.get('clean_title') or '').lower()}"
        subfield_low = (meta.get("subfield") or "").lower()
        b["_ai_meta"] = meta
        if meta.get("clean_title"):
            b["clean_title"] = meta["clean_title"]
            b["ai_level"], b["ai_kind"] = meta["level"], meta["kind"]
        # AI category fixes misfiled books; display fields stay original
        cat_str = meta.get("ai_category") or b.get("category") or ""
        tags_low = " ".join(b.get("tags") or []).lower()
        # AI keywords are broad (a chemistry primer lists 量子力学): weaker evidence than title/tags, never "strong"
        # Whole-keyword match only: query '行为' must not hit keyword '行为金融'
        ai_kw_set = {k.lower() for k in meta.get("keywords", [])}
        summary_low = (b.get("summary") or "").lower()
        toc_low = (b.get("toc_text") or "").lower()
        isbn_low = (b.get("isbn") or "").lower()
        full_doc_low = f"{title_sub} {author_low} {cat_str.lower()} {tags_low} {summary_low} {toc_low}"

        matched_anchors: List[str] = []
        matched_syns: List[str] = []
        lexical_score = 0.0
        primary_hit_count = 0
        synonym_hit_count = 0
        matched_concept_groups = 0

        # Pinyin query matching against punctuation-cleaned full pinyin & initials tail with syllable-end boundary guard
        if is_pinyin and pinyin_joined:
            py_tokens = pinyin_raw.split()
            py_full_raw = py_tokens[-2] if len(py_tokens) >= 2 else pinyin_raw
            py_init_raw = py_tokens[-1] if len(py_tokens) >= 1 else ""
            py_full_clean = re.sub(r"[^a-z0-9]", "", py_full_raw)

            # Guard against cross-syllable false positives (e.g. 'santi' matching 'santiao'=三条 or 'santian'=三天)
            if pinyin_joined.endswith("i"):
                py_re = rf"{re.escape(pinyin_joined)}(?![aeng])"
            elif pinyin_joined[-1] in ("a", "e", "o"):
                py_re = rf"{re.escape(pinyin_joined)}(?![inor])"
            elif pinyin_joined.endswith("u"):
                py_re = rf"{re.escape(pinyin_joined)}(?![aein])"
            elif pinyin_joined.endswith("n") and not pinyin_joined.endswith("ng"):
                py_re = rf"{re.escape(pinyin_joined)}(?!g)"
            else:
                py_re = re.escape(pinyin_joined)

            m_py = re.search(py_re, py_full_clean)
            if m_py:
                # Match inside title / subtitle / author head ranks far above summary mentions
                lexical_score += 220.0 if m_py.start() < 65 else 125.0
                primary_hit_count += 2
                matched_concept_groups += 1
                matched_anchors.append(f"拼音:{pinyin_joined}")
            elif pinyin_joined in py_init_raw[:35]:
                lexical_score += 195.0
                primary_hit_count += 2
                matched_concept_groups += 1
                matched_anchors.append(f"首字母:{pinyin_joined}")
            elif pinyin_joined in title_sub or pinyin_joined in author_low:
                lexical_score += 140.0
                primary_hit_count += 1
                matched_concept_groups += 1
                matched_anchors.append(pinyin_joined)

        # Exact book title / author / ISBN pin boost
        exact_title = (intent.get("explicit_title") or "").strip().lower()
        clean_et = re.sub(r"[^\w\u4e00-\u9fff]+", "", exact_title)
        # The query is read in simplified form, so a traditional title ('刻意進化') is compared simplified too
        clean_title = re.sub(r"[^\w\u4e00-\u9fff]+", "", to_simplified(title_low))
        clean_q = re.sub(r"[^\w\u4e00-\u9fff]+", "", q_exact_low)

        target_title = clean_et or clean_q
        lex_before_title_pin = lexical_score
        if not is_pinyin and target_title and len(target_title) >= 2:
            if clean_title == target_title or isbn_low == q_exact_low or author_low == q_exact_low:
                lexical_score += 480.0
                primary_hit_count += 3
                matched_concept_groups += 2
                matched_anchors.append(f"书名精准匹配:{intent.get('explicit_title') or raw_query.strip()}")
            elif clean_title.startswith(target_title):
                lexical_score += 320.0
                primary_hit_count += 2
                matched_concept_groups += 1
                matched_anchors.append(f"书名包含:{intent.get('explicit_title') or raw_query.strip()}")
            elif target_title in clean_title:
                lexical_score += 220.0
                primary_hit_count += 1
                matched_concept_groups += 1
                matched_anchors.append(f"书名包含:{intent.get('explicit_title') or raw_query.strip()}")
            elif target_title in to_simplified(sub_low):
                lexical_score += 110.0
                primary_hit_count += 1
                matched_anchors.append(f"副标题命中:{intent.get('explicit_title') or raw_query.strip()}")

        title_pin_bonus = lexical_score - lex_before_title_pin

        # Tier-1 Primary topical terms & concept group coverage matching (weighted by Semantic Role x Corpus IDF)
        strong_tier1_hit = False
        if not is_pinyin:
            lex_before_terms = lexical_score
            term_gains: List[float] = []
            for pt in primary_terms:
                pt_low = pt.lower()
                group_hit = False
                gain_start = lexical_score
                w_pt = float(term_weights.get(pt, 1.25 if len(pt_low) >= 3 else 1.0))
                field_hits = {
                    "meta": any(_has_term(pt_low, text) for text in (cat_str.lower(), tags_low, subfield_low)),
                    "summary": _has_term(pt_low, summary_low),
                    "ai_keywords": pt_low in ai_kw_set,
                    "toc": _has_term(pt_low, toc_low),
                }
                main_field, main_pts = "", 0.0
                if pt in author_terms and _has_term(pt_low, author_low):
                    # Written BY the named author beats a title/blurb that merely mentions them
                    lexical_score += 110.0 * w_pt
                    main_field, main_pts = "author_term", 110.0
                    primary_hit_count += 1
                    strong_tier1_hit = True
                    group_hit = True
                    if pt not in matched_anchors:
                        matched_anchors.append(pt)
                elif _has_term(pt_low, title_sub):
                    lexical_score += 68.0 * w_pt
                    main_field, main_pts = "title", 68.0
                    primary_hit_count += 1
                    strong_tier1_hit = True
                    group_hit = True
                    if pt not in matched_anchors:
                        matched_anchors.append(pt)
                elif _has_term(pt_low, author_low) or pt_low == isbn_low:
                    lexical_score += 74.0 * w_pt
                    main_field, main_pts = "author", 74.0
                    primary_hit_count += 1
                    strong_tier1_hit = True
                    group_hit = True
                    if pt not in matched_anchors:
                        matched_anchors.append(pt)
                elif field_hits["meta"]:
                    main_field, main_pts = "meta", FIELD_PTS["meta"]
                    lexical_score += main_pts * w_pt
                    primary_hit_count += 1
                    strong_tier1_hit = True
                    group_hit = True
                    if pt not in matched_anchors:
                        matched_anchors.append(pt)
                elif field_hits["summary"]:
                    main_field, main_pts = "summary", FIELD_PTS["summary"]
                    lexical_score += main_pts * w_pt
                    primary_hit_count += 1
                    group_hit = True
                    if pt not in matched_anchors:
                        matched_anchors.append(pt)
                elif field_hits["ai_keywords"]:
                    main_field, main_pts = "ai_keywords", FIELD_PTS["ai_keywords"]
                    lexical_score += main_pts * w_pt
                    primary_hit_count += 1  # topical, but not a covered concept group (no coverage bonus)
                    if pt not in matched_anchors:
                        matched_anchors.append(pt)
                elif field_hits["toc"]:
                    main_field, main_pts = "toc", FIELD_PTS["toc"]
                    lexical_score += main_pts * w_pt
                    primary_hit_count += 1
                    group_hit = True
                    if pt not in matched_anchors:
                        matched_anchors.append(pt)
                else:
                    # 2-char half of a 3..4 char compound (e.g. '睡眠' in '睡眠质量', '谈判' in '谈判技巧', '衰老' in '抗衰老')
                    half_pts, half, in_title = half_word_hit(pt_low, title_sub, f"{summary_low} {tags_low}")
                    stem_hit = half_pts > 0
                    if stem_hit:
                        lexical_score += half_pts * w_pt
                        primary_hit_count += 1
                        strong_tier1_hit = strong_tier1_hit or in_title
                        group_hit = True
                        if half not in matched_anchors:
                            matched_anchors.append(half)
                    if not stem_hit:
                        # Check if any direct synonym of this primary concept hits the document
                        pt_syns = DOMAIN_SYNONYM_MAP.get(pt_low) or []
                        for ps in pt_syns:
                            if _has_term(ps.lower(), full_doc_low):
                                group_hit = True
                                break
                if main_pts:
                    lexical_score += secondary_field_bonus(main_field, main_pts, field_hits) * w_pt
                if group_hit:
                    matched_concept_groups += 1
                term_gains.append(lexical_score - gain_start)

            # Alternatives ('A或者B'): score the best-matching alternative, not the sum of both
            if is_disjunctive and term_gains:
                lexical_score = lex_before_terms + max(term_gains)

            # Synonym / Cross-lingual domain expansions matching (capped to prevent bilingual title keyword stuffing)
            syn_score = 0.0
            for syn in synonym_terms:
                syn_low = syn.lower()
                if syn_low in matched_anchors:
                    continue
                if _has_term(syn_low, title_sub):
                    syn_score += 28.0
                    synonym_hit_count += 1
                    if syn not in matched_syns:
                        matched_syns.append(syn)
                elif _has_term(syn_low, summary_low) or _has_term(syn_low, tags_low) or _has_term(syn_low, cat_str.lower()):
                    syn_score += 16.0
                    synonym_hit_count += 1
                    if syn not in matched_syns:
                        matched_syns.append(syn)
                elif _has_term(syn_low, toc_low):
                    syn_score += 8.0
                    synonym_hit_count += 1
                    if syn not in matched_syns:
                        matched_syns.append(syn)
            lexical_score += min(45.0, syn_score)

        # Superlinear Multi-Concept Coverage Bonus across Tier-1 core concepts
        if len(primary_terms) >= 2 and matched_concept_groups >= 2 and not is_disjunctive:
            lexical_score += 38.0 * (matched_concept_groups - 1)

        # Negative constraint penalty (e.g. '不要鸡汤', '不看小说')
        neg_penalty = 0.0
        for neg_t in negative_terms:
            if neg_t.lower() in full_doc_low:
                neg_penalty -= 120.0

        # Tier-2 Context & Demographic Modifiers with 1.5x Synergy Gate
        # Only awards synergy points when the book also hits a Tier-1 core concept (or its domain synonyms)
        tier2_hit_count = 0
        has_core_or_syn = (primary_hit_count > 0) or (synonym_hit_count > 0)
        for pn in parent_nouns:
            pn_low = pn.lower()
            w_pn = float(term_weights.get(pn, 0.50))
            if has_core_or_syn:
                if _has_term(pn_low, title_sub) or _has_term(pn_low, cat_str.lower()) or _has_term(pn_low, tags_low):
                    lexical_score += 32.0 * w_pn * 1.5
                    tier2_hit_count += 1
                    if pn not in matched_anchors:
                        matched_anchors.append(pn)
                elif _has_term(pn_low, summary_low) or _has_term(pn_low, toc_low):
                    lexical_score += 20.0 * w_pn * 1.5
                    tier2_hit_count += 1
                    if pn not in matched_anchors:
                        matched_anchors.append(pn)
            else:
                # Without Tier-1 hit, Tier-2 context modifier gets minimal weight and never triggers topical hit
                if _has_term(pn_low, title_sub) or _has_term(pn_low, cat_str.lower()):
                    lexical_score += 4.0 * w_pn

        has_topical_hit = has_core_or_syn

        # Polysemy Disambiguation Guard:
        # If query has a short 2-char Tier-1 word (e.g. '敏感') qualified by Tier-2 context (e.g. '心理', '人群'),
        # reject cross-domain books (like '关键对话' mentioning '敏感话题' in summary) that miss all context & synonyms
        if (
            not is_pinyin
            and len(primary_terms) == 1
            and len(primary_terms[0]) == 2
            and (parent_nouns or inferred_cats)
            and not strong_tier1_hit
            and tier2_hit_count == 0
            and synonym_hit_count == 0
            and (not inferred_cats or cat_str not in inferred_cats)
        ):
            lexical_score *= 0.15
            has_topical_hit = False

        # bge-m3 Dense cosine similarity (typically in 0.40 .. 0.85 range)
        cos_sim = float(dense_scores.get(bid, 0.0))
        dense_pts = max(0.0, (cos_sim - 0.35) * 105.0) if cos_sim > 0 else 0.0

        # Category alignment prior
        cat_pts = 0.0
        in_target_cat = False
        if inferred_cats:
            if cat_str == inferred_cats[0]:
                cat_pts = 28.0
                in_target_cat = True
            elif cat_str in inferred_cats[1:]:
                cat_pts = 18.0
                in_target_cat = True
            elif not has_topical_hit:
                cat_pts = -45.0

        # Classic score contribution (strongly amplified when user asks for "经典 / 必读 / 代表作")
        c_score = float(b.get("classic_score") or 20.0)
        if wants_classic:
            classic_pts = c_score * 1.45
            if c_score >= 45.0 and (has_topical_hit or in_target_cat):
                classic_pts += 26.0
        else:
            classic_pts = c_score * 0.22

        # Style / difficulty modifier bonus
        style_pts = 0.0
        style_reason = ""
        level, kind = meta.get("level", ""), meta.get("kind", "")
        if style_mode == "hardcore_textbook":
            if level:
                is_popsci = level == "入门"
                is_textbook = level == "专业" or kind == "学术理论"
            else:
                is_popsci = any(pm in title_sub or pm in summary_low[:120] for pm in POPSCI_COMIC_MARKERS)
                is_textbook = any(tm in title_sub or tm in summary_low for tm in TEXTBOOK_POSITIVE_MARKERS)
            if is_popsci:
                style_pts = -40.0
            elif is_textbook and (has_topical_hit or cos_sim >= 0.54):
                style_pts = 26.0
                style_reason = "硬核教材/专著契合"
        elif style_mode == "intro_popsci":
            # Title-only check: summaries often say "从入门到进阶", titles don't lie about level
            title_advanced = any(am in title_sub for am in ADVANCED_LEVEL_MARKERS) and not any(
                im in title_sub for im in INTRO_LEVEL_MARKERS
            )
            if level:  # DeepSeek level tag beats keyword guessing, but it is noisy: only 专业 gets the full penalty
                is_popsci = level in ("入门", "普及")
                is_advanced = title_advanced or level == "专业"
                title_level = level == "入门"
            else:
                is_popsci = any(
                    pm in title_sub or pm in summary_low
                    for pm in POPSCI_COMIC_MARKERS + INTRO_LEVEL_MARKERS
                )
                is_advanced = title_advanced
                title_level = any(im in title_sub for im in INTRO_LEVEL_MARKERS + POPSCI_COMIC_MARKERS)
            # Level is a hard requirement for "零基础 / 小白" asks: weigh it on par with a core-term hit
            if is_advanced:
                style_pts = -60.0
            elif level == "进阶":
                style_pts = -20.0
            elif is_popsci and (has_topical_hit or cos_sim >= 0.54):
                style_pts = 55.0 if title_level else 40.0
                if not strong_tier1_hit and cos_sim < 0.58:  # weak topical link: a generic primer is not the answer
                    style_pts /= 2
                style_reason = "通俗入门契合"
        elif style_mode == "technical_depth":
            # A marker that is the query's own topic ('投资原理') is not evidence of non-technical framing
            query_words = " ".join(primary_terms + synonym_terms).lower()
            if kind:  # DeepSeek kind tag beats keyword guessing
                is_tech = kind == "技术工程"
                non_tech = kind in _NON_TECH_KINDS and not any(w in query_words for w in _NON_TECH_KINDS[kind])
            else:
                tech_text = f"{title_sub} {summary_low} {toc_low}"
                is_tech = sum(1 for m in TECH_DEPTH_MARKERS if m in tech_text) >= 2
                non_tech = any(
                    m in f"{title_sub} {summary_low[:80]}" for m in NON_TECH_MARKERS if m not in query_words
                )
            if is_tech and (has_topical_hit or cos_sim >= 0.54):
                style_pts = 40.0
                style_reason = "技术原理契合"
            elif non_tech:
                style_pts = -40.0

        # Box-set / Anthology damping unless explicitly requested
        is_anth = int(b.get("is_anthology") or 0) == 1
        anth_penalty = 0.0
        if is_anth and not wants_anthology:
            anth_penalty = ANTHOLOGY_PENALTY

        # Topical + Semantic Rescue Gate: allow high-confidence bge-m3 semantic matches (cos_sim >= 0.54)
        prelim_score = (
            lexical_score + dense_pts + cat_pts + classic_pts + style_pts + anth_penalty + neg_penalty
        )
        if not has_topical_hit and cos_sim < 0.54:
            prelim_score = prelim_score * 0.12 - 50.0

        b["_prelim_score"] = prelim_score
        b["_lexical_score"] = lexical_score
        b["_cos_sim"] = cos_sim
        b["_has_topical_hit"] = has_topical_hit
        b["_in_target_cat"] = in_target_cat
        b["_matched_anchors"] = matched_anchors
        b["_matched_syns"] = matched_syns
        b["_matched_groups"] = matched_concept_groups
        b["_style_reason"] = style_reason
        b["title_pin_bonus"] = title_pin_bonus
        prelim_pool.append(b)

    prelim_pool.sort(key=lambda x: x["_prelim_score"], reverse=True)

    return prelim_pool
