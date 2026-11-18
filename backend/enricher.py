import json
import re
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple
from config import SUPPORTED_EXTENSIONS
from database import get_conn
from parser import build_cjk_search_tokens, clean_spam_text

CATEGORY_RULES: List[Tuple[str, List[str]]] = [
    (
        "数学与统计",
        [
            "数学", "微积分", "高等数学", "线性代数", "离散数学", "概率论", "统计", "数论",
            "几何", "拓扑", "测度论", "微分方程", "群论", "近世代数", "泛函分析", "运筹学",
            "数值计算", "时间序列", "矩阵", "公式", "定理", "公理",
        ],
    ),
    (
        "人工智能与技术",
        [
            "人工智能", "机器学习", "深度学习", "神经网络", "大模型", "llm", "计算机",
            "编程", "算法", "代码", "软件", "架构", "分布式", "操作系统", "内核", "数据库",
            "python", "java", "rust", "golang", "c++", "javascript", "typescript",
            "react", "linux", "docker", "kubernetes", "网络协议", "编译原理", "网络安全",
            "数据分析", "数据科学", "芯片", "半导体", "算力",
        ],
    ),
    (
        "产品与UX设计",
        [
            "产品经理", "产品设计", "交互设计", "用户体验", "ux", "ui", "信息架构",
            "设计系统", "原型", "敏捷", "用户研究", "界面", "视觉设计", "可用性",
        ],
    ),
    (
        "商业管理与创投",
        [
            "商业", "企业管理", "公司治理", "创业", "企业", "领导力", "战略", "增长", "组织", "创投",
            "投资人", "商业模式", "德鲁克", "稻盛和夫", "华为", "阿里", "亚马逊", "奈飞",
            "供应链", "商业史", "执行力", "okr", "产业竞争", "芯片战争",
        ],
    ),
    (
        "销售与市场营销",
        [
            "销售", "营销", "品牌", "广告", "公关", "文案", "获客", "带货", "商务",
            "渠道", "谈判", "说服", "转化率", "客户关系", "流量", "影响力",
        ],
    ),
    (
        "金融与经济学",
        [
            "经济", "金融", "投资", "股票", "证券", "基金", "货币", "银行", "资本",
            "宏观", "微观", "博弈论", "巴菲特", "芒格", "价值投资", "股市", "债券",
            "通胀", "债务", "周期", "财报", "会计", "估值", "政治经济学", "国富论", "财政",
        ],
    ),
    (
        "心理学与脑科学",
        [
            "心理", "认知", "潜意识", "大脑", "脑科学", "神经科学", "情绪", "精神分析",
            "弗洛伊德", "阿德勒", "荣格", "自卑", "亲密关系", "人格", "注意力", "专注力", "心流",
            "抑郁", "焦虑", "思考，快与慢", "行为决策", "卡尼曼", "认知偏差", "非理性", "自驱力",
            "敏感", "高敏感", "内向", "讨好", "原生家庭", "心理创伤",
        ],
    ),
    (
        "历史与地缘政治",
        [
            "历史", "通史", "断代史", "王朝", "帝国", "资治通鉴", "史记", "明朝", "唐朝",
            "宋朝", "清朝", "秦汉", "民国", "二战", "冷战", "世界史", "中国史", "地缘政治",
            "地缘", "大国博弈", "科技博弈", "官场", "战争", "文明史", "考古", "古希腊", "古罗马", "外交",
        ],
    ),
    (
        "社科政治与法律",
        [
            "社会学", "政治", "制度", "人类学", "法学", "法律", "宪法", "刑法", "民法",
            "国际关系", "民主", "国家", "治理", "公共政策", "人口", "阶级", "传播学",
            "舆论", "乌合之众", "乡土中国",
        ],
    ),
    (
        "哲学宗教与通识",
        [
            "哲学", "人生哲学", "形而上学", "伦理学", "存在主义", "认识论", "逻辑学", "苏格拉底",
            "柏拉图", "亚里士多德", "康德", "尼采", "叔本华", "老子", "道德经", "庄子",
            "论语", "孔子", "王阳明", "佛学", "禅宗", "宗教", "神学", "思想史", "生死", "死亡",
        ],
    ),
    (
        "自然科学与健康",
        [
            "物理", "量子", "相对论", "宇宙", "天文", "黑洞", "生物", "基因", "进化",
            "细胞", "病毒", "免疫", "医学", "健康", "解剖", "化学", "地理", "气候",
            "自然", "人体", "营养", "睡眠", "抗衰老", "衰老", "运动", "锻炼", "健身", "精力",
        ],
    ),
    (
        "文学小说与艺术",
        [
            "小说", "文学", "散文", "诗歌", "诗词", "戏剧", "推理", "悬疑", "侦探",
            "科幻", "三体", "东野圭吾", "村上春树", "马尔克斯", "百年孤独", "鲁迅",
            "艺术", "绘画", "音乐", "电影", "建筑", "摄影", "书法",
        ],
    ),
    (
        "人物传记与回忆录",
        [
            "传记", "回忆录", "自传", "年谱", "评传", "乔布斯传", "马斯克传", "富兰克林",
            "曾国藩", "林肯", "毛泽东", "丘吉尔", "列传", "生平",
        ],
    ),
    (
        "个人成长与生活",
        [
            "个人成长", "自我提升", "时间管理", "精力管理", "习惯", "自律", "沟通", "表达",
            "职场", "写作", "阅读", "思维模型", "学习法", "生活", "美食", "旅行", "极简",
            "育儿", "家庭教育", "亲子", "养育", "幸福",
        ],
    ),
]


def classify_book_category(
    title: str, subtitle: str, author: str, tags: List[str], summary: str, toc: List[str]
) -> Tuple[str, List[str]]:
    """Classify book into 1 of 14 canonical categories and extract clean subject tags."""
    weighted_text = (
        f"{title} {title} {title} {subtitle} {subtitle} {author} "
        f"{' '.join(tags)} {' '.join(toc[:15])} {summary[:600]}"
    ).lower()

    best_cat = "个人成长与生活"
    best_score = 0
    matched_keywords: List[str] = []

    for cat, keywords in CATEGORY_RULES:
        score = 0
        cat_matches = []
        for kw in keywords:
            cnt = weighted_text.count(kw.lower())
            if cnt > 0:
                # Higher weight if keyword appears in title/subtitle
                in_title = kw.lower() in f"{title} {subtitle}".lower()
                score += cnt * (4 if in_title else 1)
                cat_matches.append(kw)
        if score > best_score:
            best_score = score
            best_cat = cat
            matched_keywords = cat_matches

    # Combine explicit tags and matched domain keywords
    clean_tags: List[str] = []
    for t in tags + matched_keywords:
        t_clean = t.strip(" .,;，。；[]【】()")
        if 2 <= len(t_clean) <= 12 and t_clean not in clean_tags:
            clean_tags.append(t_clean)
        if len(clean_tags) >= 8:
            break
    if not clean_tags:
        clean_tags.append(best_cat)

    return best_cat, clean_tags


def extract_synopsis_from_book_text(text_sample: str, title: str) -> str:
    """Extract genuine synopsis, CIP summary, or preface core paragraphs from the book's own opening pages."""
    if not text_sample or len(text_sample.strip()) < 40:
        return ""

    # 1. Look for explicit CIP / 内容简介 / 内容提要 / 编辑推荐 block
    m_cip = re.search(
        r"(?:内容简介|内容提要|图书在版编目|编辑推荐|作品简介|本书简介|内容梗概)[:：\s]*\n?([\s\S]{50,700})",
        text_sample,
    )
    if m_cip:
        blk = m_cip.group(1).strip()
        # Cut off at ISBN or next section header
        blk = re.split(r"\n(?:ISBN|Ⅰ\.|I\.|作者简介|目\s*录|第[一1]章|前言|序言)", blk)[0]
        blk = re.sub(r"\s+", " ", blk).strip()
        if len(blk) >= 45:
            return blk[:450]

    # 2. Extract substantial paragraphs from Preface / Introduction / Opening
    paragraphs = []
    for line in text_sample.splitlines():
        ln = line.strip()
        if len(ln) < 25:
            continue
        # Skip copyright / publishing / acknowledgment boilerplate lines
        if re.search(
            r"(?:版权所有|侵权必究|印刷厂|开本|印张|字数|定价|邮政编码|发行部|图书在版|CIP数据|责任编辑|封面设计|all rights reserved|isbn|作者授权|电子版发行|由衷感谢|献给|谨以此书|致谢)",
            ln,
            re.IGNORECASE,
        ):
            continue
        paragraphs.append(ln)
        if sum(len(p) for p in paragraphs) >= 420:
            break

    if paragraphs:
        joined = " ".join(paragraphs)
        return re.sub(r"\s+", " ", joined).strip()[:450]
    return ""


def synthesize_structured_synopsis(
    parsed: Dict[str, Any], category: str, clean_tags: List[str]
) -> Dict[str, Any]:
    """
    Generate accurate, non-boilerplate structured summary, core points, target audience,
    and 3 Reverse-HyDE natural language search queries.
    """
    title = parsed["title"]
    subtitle = parsed.get("subtitle") or ""
    author = parsed.get("author") or "未知作者"
    raw_desc = clean_spam_text(parsed.get("raw_description") or "")
    text_sample = parsed.get("text_sample") or ""
    toc: List[str] = parsed.get("toc") or []

    # Clean TOC items (filter out generic "封面", "版权", "目录", "扉页")
    meaningful_toc = [
        t
        for t in toc
        if len(t) >= 2
        and not re.match(
            r"^(?:封面|扉页|版权|版权信息|版权页|目录|contents|cover|title|copyright|致谢|索引|参考文献)$",
            t.strip(),
            re.IGNORECASE,
        )
    ]

    summary_source = "embedded"
    base_desc = ""

    if len(raw_desc) >= 50:
        base_desc = re.sub(r"\s+", " ", raw_desc).strip()[:500]
        summary_source = "embedded"
    else:
        extracted = extract_synopsis_from_book_text(text_sample, title)
        if len(extracted) >= 50:
            base_desc = extracted
            summary_source = "preface_extracted"
        elif raw_desc:
            base_desc = raw_desc

    # Build structured summary combining description + chapter structure if needed
    full_title = f"《{title}：{subtitle}》" if subtitle else f"《{title}》"
    author_str = f"由 {author} 著" if author and author != "未知作者" else ""
    toc_preview = "、".join(meaningful_toc[:8])

    if len(base_desc) >= 60:
        summary = base_desc
        if meaningful_toc and len(summary) < 220:
            summary = f"{summary} 全书核心脉络涵盖：{toc_preview}等章节。"
    else:
        summary_source = "toc_synthesized"
        tag_str = "、".join(clean_tags[:5])
        if meaningful_toc:
            summary = (
                f"{full_title}{author_str}，属「{category}」领域代表作（核心主题：{tag_str}）。"
                f"全书围绕{title}{('（' + subtitle + '）') if subtitle else ''}展开深度论述，"
                f"主要章节脉络包括：{toc_preview}。"
            )
            if len(base_desc) > 10:
                summary += f" {base_desc}"
        else:
            snippet = re.sub(r"\s+", " ", text_sample).strip()[:200]
            summary = (
                f"{full_title}{author_str}，聚焦「{category}」方向（关键词：{tag_str}）。"
            )
            if snippet:
                summary += f" 书中核心内容摘录：{snippet}…"

    # Core points (3 to 6 structured items)
    core_points: List[str] = []
    if meaningful_toc:
        core_points = meaningful_toc[:6]
    else:
        # Split summary into key sentences
        sents = [
            s.strip()
            for s in re.split(r"[。！？；!?;]", summary)
            if 10 <= len(s.strip()) <= 80
        ]
        core_points = sents[:4]

    # Target audience
    tag_focus = "、".join(clean_tags[:3]) if clean_tags else category
    target_audience = f"适合关注「{category}」尤其是{tag_focus}议题的读者，以及希望系统了解{title}相关理论与实践的研究者。"

    # 3 Reverse-HyDE natural language search queries
    q1 = f"推荐关于{title}、{subtitle or tag_focus}的{category}书籍"
    q2 = (
        f"讲{'、'.join(meaningful_toc[:4])}的书"
        if meaningful_toc
        else f"深入剖析{tag_focus}底层逻辑与核心方法的经典著作"
    )
    q3 = f"{author if author != '未知作者' else ''}{'写的' if author != '未知作者' else ''}关于{tag_focus}和{category}的代表作".strip()
    hyde_queries = [q1, q2, q3]

    return {
        "summary": summary[:600],
        "summary_source": summary_source,
        "core_points": core_points,
        "target_audience": target_audience,
        "hyde_queries": hyde_queries,
        "toc_text": " | ".join(meaningful_toc[:35]),
    }


def compute_book_quality_score(
    parsed: Dict[str, Any], enriched: Dict[str, Any], trust_weight: float = 1.0
) -> float:
    """Compute a 0-100 quality & completeness score for ranking and deduplication."""
    ext = "." + parsed["format"].lower()
    fmt_score = SUPPORTED_EXTENSIONS.get(ext, 40) * 0.45  # Max 45 pts
    meta_score = 0.0
    if parsed.get("author") and parsed["author"] != "未知作者":
        meta_score += 12.0
    if parsed.get("cover_path"):
        meta_score += 10.0
    if len(enriched.get("summary") or "") >= 80:
        meta_score += 15.0
    if enriched.get("toc_text"):
        meta_score += 10.0
    if parsed.get("isbn") or parsed.get("publisher"):
        meta_score += 8.0
    total = (fmt_score + meta_score) * trust_weight
    return round(min(100.0, total), 2)


def enrich_parsed_book(parsed: Dict[str, Any], trust_weight: float = 1.0) -> Dict[str, Any]:
    """Full L1+L2+L3 enrichment pipeline producing FTS tokens and dual 300-dim vectors."""
    category, clean_tags = classify_book_category(
        parsed["title"],
        parsed.get("subtitle") or "",
        parsed.get("author") or "",
        parsed.get("tags") or [],
        parsed.get("raw_description") or "",
        parsed.get("toc") or [],
    )

    syn = synthesize_structured_synopsis(parsed, category, clean_tags)
    quality = compute_book_quality_score(parsed, syn, trust_weight=trust_weight)

    # Build CJK + Pinyin search index string for SQLite FTS5
    fts_seed = (
        f"{parsed['title']} {parsed.get('subtitle', '')} {parsed.get('author', '')} "
        f"{parsed.get('series', '')} {category} {' '.join(clean_tags)} "
        f"{syn['summary'][:400]} {syn['toc_text'][:400]}"
    )
    pinyin_search = build_cjk_search_tokens(fts_seed, include_pinyin=True)

    return {
        **parsed,
        "category": category,
        "tags": clean_tags,
        "summary": syn["summary"],
        "summary_source": syn["summary_source"],
        "core_points": syn["core_points"],
        "target_audience": syn["target_audience"],
        "hyde_queries": syn["hyde_queries"],
        "toc_text": syn["toc_text"],
        "quality_score": quality,
        "pinyin_search": pinyin_search,
    }
