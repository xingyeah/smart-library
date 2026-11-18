import re
from typing import Any, Dict, List, Set, Tuple

# Short ASCII terms ('ai', 'ui', 'r', 'c++') must match whole words: plain substring hits 'brain', 'chain', 'detailed'
_SHORT_ASCII_TERM = re.compile(r"[a-z0-9][a-z0-9+#.]{0,3}")
_ENGLISH_WORD = re.compile(r"[a-z]{5,}")  # longer than the short-term rule below; plain English words
_TERM_RE_CACHE: Dict[str, Any] = {}


def _has_term(term_low: str, text_low: str) -> bool:
    if _ENGLISH_WORD.fullmatch(term_low):
        # Whole word, singular or plural: 'cryptocurrency' finds 'cryptocurrencies', 'startup' finds 'startups'
        pat = _TERM_RE_CACHE.get(term_low)
        if pat is None:
            stem = term_low[:-1] if term_low.endswith("y") else term_low
            tail = "(?:y|ies)" if term_low.endswith("y") else "(?:s|es)?"
            pat = re.compile(rf"(?<![a-z0-9]){re.escape(stem)}{tail}(?![a-z0-9])")
            _TERM_RE_CACHE[term_low] = pat
        return pat.search(text_low) is not None
    if not _SHORT_ASCII_TERM.fullmatch(term_low):
        return term_low in text_low
    pat = _TERM_RE_CACHE.get(term_low)
    if pat is None:
        pat = re.compile(rf"(?<![a-z0-9]){re.escape(term_low)}(?![a-z0-9])")
        _TERM_RE_CACHE[term_low] = pat
    return pat.search(text_low) is not None


# Kinds a non-technical framing comes from, and the query words that make that kind the topic itself
_NON_TECH_KINDS = {"投资理财": ("投资", "理财"), "纪实调查": ("纪实", "调查"), "传记回忆": ("传记", "回忆"),
                   "小说文学": ("小说",), "随笔评论": ("随笔", "评论")}

_STRIP_PREFS: Tuple[str, ...] = (
    "我想看关于", "我想找关于", "求推荐关于", "推荐几本关于", "推荐关于",
    "我想看", "我想读", "我想找", "我想了解", "想看", "想读", "想找",
    "求推荐", "推荐几本", "推荐一些", "推荐", "有没有关于", "有没有",
    "关于", "有关", "寻找", "请问有哪些", "有哪些",
)

_STRIP_SUFS: Tuple[str, ...] = (
    "应该如何正确处理", "应该怎么正确处理", "该如何正确处理", "如何正确处理", "怎么正确处理",
    "应该如何处理", "应该怎么处理", "该如何处理", "如何处理", "怎么处理",
    "应该如何解决", "应该怎么解决", "如何解决", "怎么解决", "如何应对", "怎么应对", "如何克服", "怎么克服",
    "相关的硬核教材", "相关的经典书籍", "相关的经典书", "相关的深度历史书",
    "相关的历史书", "相关的教材", "相关的书籍", "相关的书", "相关的读物",
    "相关的著作", "相关的", "相关书籍", "相关",
    "方面的经典书籍", "方面的书籍", "方面的书", "领域的经典书籍", "领域的书籍", "领域的书",
    "的硬核教材", "的经典书籍", "的经典书", "的经典著作", "的经典作品", "的必读书籍",
    "的深度历史书", "的历史书", "的教材", "的教科书", "的入门书", "的书籍", "的书",
    "的读物", "的著作", "的好书",
    "硬核教材", "经典书籍", "经典书", "经典著作", "经典作品", "经典必读",
    "必读书籍", "必读书目", "必读", "经典", "代表作", "权威教材",
    "教材", "教科书", "讲义", "入门书籍", "入门书", "入门", "书籍", "图书", "读物", "好书",
)

# Conversational prefixes/verbs/frames to strip from clauses before concept extraction
_CONVERSATIONAL_FRAME_REGEX = re.compile(
    r"(?:我想看关于|我想找关于|求推荐关于|推荐几本关于|推荐关于|有没有适合|有没有关于|"
    r"我想看|我想读|我想找|我想了解|想看|想读|想找|求推荐|推荐几本|推荐一些|推荐|"
    r"有没有|有哪些|有什么|请问|寻找|看看|帮我找|"
    r"讲人类为什么会犯|为什么会犯|为什么会走向|为什么会|为什么|"
    r"应该如何正确处理|应该怎么正确处理|该如何正确处理|如何正确处理|怎么正确处理|"
    r"应该如何处理|应该怎么处理|该如何处理|如何处理|怎么处理|应该如何|应该怎么|如何正确|怎么正确|"
    r"怎么提高自己的|如何提高自己的|怎么提高|如何提高|如何培养孩子的|怎么培养孩子的|如何培养|怎么培养|"
    r"如何改善|怎么改善|如何科学|怎么科学|在职场里更好地|在职场里更有|在职场里|更好地|更有|"
    r"真正有深度的|有深度的|适合零基础小白入门|适合零基础|适合小白|适合|"
    r"应该|正确|处理|解决|应对|克服|受|影响做出|做出|走向|面对|关于|有关|讲解|讲透|讲述|介绍|剖析|探讨|研究|理解|讲)"
)


# Tier-3 Low-Information Words: conversational filler, modal auxiliaries, generic action verbs & evaluative adjectives
# Weight = 0.0 (never treated as topical keywords, never shown in match reasons or counted in multi-concept bonus)
CONVERSATIONAL_STOPWORDS: Set[str] = {
    "基于", "基於", "关于", "我想", "想看", "想读", "想找", "看看", "寻找", "有关", "相关", "相关的",
    "哪些", "有什么", "有没有", "没有", "推荐", "求推荐", "找一本", "想要", "请问",
    "书籍", "的书籍", "的书", "书目", "读物", "图书", "好书", "佳作", "作品", "著作",
    "一本", "一部", "几本", "一些", "这些", "那些", "以及", "并且", "或者", "或是", "还是",
    "能够", "可以", "怎么", "如何", "怎样", "什么", "什么样的", "为何", "为什么", "为什么会",
    "应该", "应当", "需要", "必须", "怎么才能", "如何正确", "应该如何",
    "比较", "非常", "特别", "类似", "那种", "本书", "全书", "方面", "领域", "问题", "现象", "原因",
    "正确", "有效", "合理", "科学", "高效", "更好", "更好地", "快速", "真正", "全面", "常见", "主要", "基本", "基础", "重要", "严重", "日常",
    "处理", "解决", "应对", "面对", "克服", "改善", "提高", "提升", "培养", "建立", "掌握", "学习", "了解", "认识",
    "分析", "剖析", "探讨", "研究", "理解", "讲述", "讲透", "讲解", "介绍", "看懂", "学会", "做到", "进行", "使用",
    "利用", "实现", "改变", "调整", "保持", "避免", "防止", "走出", "摆脱", "适合", "做出", "走向",
    "方法", "方式", "办法", "策略", "指南", "技巧", "能力",
    "自己", "自己的", "我们", "不要", "不看", "太枯燥", "枯燥",
    "更有", "里更", "在职场", "怎么提高", "如何培养", "如何科学", "为什么会犯",
    "的", "了", "和", "与", "或", "及", "是", "在", "有", "我", "你", "他", "它", "者",
}
# English filler in natural-language queries ('books about X for beginners'); 'st'/'nd'/'th' are left over from '21st'
ENGLISH_STOPWORDS: Set[str] = {
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "from", "by", "with", "about", "and", "or", "as",
    "is", "are", "was", "be", "it", "its", "this", "that", "these", "those", "into", "over", "between",
    "i", "me", "my", "we", "you", "your", "want", "need", "looking", "find", "read", "reading", "learn",
    "book", "books", "ebook", "any", "some", "best", "good", "great", "top", "recommend", "recommended",
    "how", "what", "why", "which", "who", "when", "do", "does", "can", "there", "written", "explain", "explains",
    "beginner", "beginners", "dummies", "st", "nd", "rd", "th",
}
CONVERSATIONAL_STOPWORDS |= ENGLISH_STOPWORDS

# Modifiers that express "classic / canonical / high reputation" intent rather than a book topic
CLASSIC_INTENT_MODIFIERS: Set[str] = {
    "经典", "经典书籍", "经典书", "经典之作", "经典著作", "经典作品", "必读", "必读书目",
    "必读书籍", "权威", "代表作", "名著", "神作", "巅峰", "口碑", "高分", "大师",
    "传世", "奠基", "圣经", "天花板", "殿堂级",
}

# Modifiers that express "hardcore / academic textbook" style intent
HARDCORE_STYLE_MODIFIERS: Set[str] = {
    "硬核", "教材", "教科书", "讲义", "导论", "教程", "专著", "推导", "证明",
    "深度", "深入", "进阶", "高阶", "学术", "系统", "理论", "专业",
}

# Modifiers that express "beginner / pop-sci / easy reading" style intent
POPSCI_STYLE_MODIFIERS: Set[str] = {
    "入门", "通俗", "通俗易懂", "深入浅出", "科普", "趣味", "漫画", "轻松",
    "零基础", "初学者", "小白", "图解", "极简", "故事", "不枯燥", "不要太枯燥", "好读", "易读",
    "beginner", "for dummies", "introductory", "easy to read",
}

# Modifiers that explicitly request multi-book box-sets / anthologies
ANTHOLOGY_MODIFIERS: Set[str] = {
    "套装", "合集", "全集", "作品集", "丛书", "系列", "全套",
}

TEXTBOOK_POSITIVE_MARKERS = [
    "教材", "教科书", "讲义", "导论", "教程", "原理", "专著", "定理", "证明",
    "公理", "习题", "推导", "数学基础", "具体数学", "高等", "理论",
    "introduction to", "guide to", "textbook", "course", "principles",
    "structures", "foundations", "fundamentals", "lectures", "handbook",
    "mathematics for computer science",
]

POPSCI_COMIC_MARKERS = [
    "漫画", "火柴人", "烧掉", "欢乐", "趣味", "游戏", "图解", "极简",
    "一分钟", "轻松", "故事", "演义", "小说",
]

# Beginner-level markers (title / summary) rewarded when the user asks for 入门 / 零基础 / 不要太枯燥
INTRO_LEVEL_MARKERS = [
    "入门", "通俗", "初学", "零基础", "小白", "新手", "快速上手", "从零",
    "少儿", "beginner", "for dummies", "crash course", "head first", "for non-", "gentle introduction",
    "hello world", "for kids",
]

# "How it works underneath" asks: '比特币的底层技术', '区块链技术原理'
TECH_DEPTH_MODIFIERS: Set[str] = {
    "底层", "原理", "机制", "架构", "协议", "源码", "技术细节", "如何运作", "怎么运作", "how it works",
}
# Evidence a book explains the technology itself (title, summary or TOC); 2+ distinct hits count
TECH_DEPTH_MARKERS = [
    "共识", "分布式", "账本", "协议", "架构", "密码学", "加密算法", "哈希", "智能合约", "工作量证明", "权益证明",
    "算法", "源码", "开发", "编程", "底层", "p2p", "consensus", "protocol", "architecture", "cryptograph",
    "distributed", "ledger", "smart contract",
]
# Investing / narrative framing that a technical ask does not want first
NON_TECH_MARKERS = [
    "投资", "资产配置", "加减仓", "估值", "致富", "财富", "交易所", "讲述", "记录", "纪实", "调查", "曝光",
    "历程", "崛起史", "传记", "回忆录", "洗钱", "诈骗", "investing", "investor",
]

# Advanced-level title markers penalized for beginner requests ('进阶 (Advanced Data Science ...)')
ADVANCED_LEVEL_MARKERS = [
    "进阶", "高级", "高阶", "精通", "深入", "专家",
    "advanced", "mastering", "expert", "in depth", "deep dive",
]

# Known English technical/domain keywords (distinguished from Chinese Pinyin queries)
KNOWN_ENGLISH_TECH_TERMS: Set[str] = {
    "python", "java", "rust", "golang", "javascript", "typescript", "react", "vue",
    "linux", "docker", "kubernetes", "sql", "mysql", "redis", "mongodb", "git",
    "ai", "llm", "agi", "gpt", "transformer", "agent", "ux", "ui", "okr", "kpi", "mba",
    "c++", "c#", "ios", "android", "aws", "azure", "api", "http", "tcp", "cpu", "gpu",
    "math", "calculus", "algebra", "statistics", "probability", "marketing", "branding",
    "finance", "investing", "economics", "psychology", "philosophy", "history", "design",
}

# Ambiguous short domain keys that must NOT match inside personal compound words (e.g. "精力管理")
AMBIGUOUS_SHORT_DOMAIN_KEYS: Set[str] = {"管理", "经济", "设计", "产品", "历史", "心理"}

# Comprehensive NLU Concept & Domain Ontology (longest keys matched first with span locking)
DOMAIN_SYNONYM_MAP: Dict[str, List[str]] = {
    # Psychology, Cognition & Behavioral Decision
    "认知偏差": ["认知错误", "思维谬误", "非理性", "思考，快与慢", "清醒思考", "行为经济学", "卡尼曼", "cognitive bias"],
    "认知错误": ["认知偏差", "思维谬误", "清醒思考", "非理性", "思考，快与慢", "盲目思考"],
    "心理偏见": ["偏见", "认知偏差", "刻板印象", "盲目思考", "思考，快与慢", "非理性"],
    "偏见": ["认知偏差", "思维谬误", "刻板印象", "盲目思考", "bias"],
    "非理性决定": ["非理性决策", "非理性", "怪诞行为学", "思考，快与慢", "认知偏差", "行为经济学"],
    "非理性决策": ["非理性", "怪诞行为学", "思考，快与慢", "认知偏差", "行为经济学", "决策"],
    "非理性": ["怪诞行为学", "认知偏差", "思考，快与慢", "行为经济学", "irrational"],
    "潜意识": ["无意识", "精神分析", "弗洛伊德", "荣格", "大脑", "subconscious", "unconscious"],
    "心理学": ["心理学与脑科学", "认知心理学", "社会心理学", "潜意识", "psychology"],
    "认知": ["心理学与脑科学", "认知心理学", "认知偏差", "思维", "大脑", "cognition", "cognitive"],
    "脑科学": ["心理学与脑科学", "神经科学", "大脑", "neuroscience", "brain"],
    "专注力": ["注意力", "心流", "深度工作", "focus", "stolen focus", "attention"],
    "自驱力": ["内驱力", "驱动力", "动机", "自律", "自我激励", "drive", "motivation"],
    "内驱力": ["自驱力", "驱动力", "动机", "自我决定论", "drive"],
    "亲密关系": ["爱情", "婚姻", "伴侣", "依恋", "情感", "社会心理学"],
    "原生家庭": ["家庭创伤", "父母", "心理治疗", "亲密关系"],
    "高敏感": ["敏感", "高度敏感", "高敏感人群", "玻璃心", "情绪敏感", "内向", "心理边界"],
    "敏感": ["高敏感", "高度敏感", "高敏感人群", "玻璃心", "情绪敏感", "心理边界", "内向"],
    "内向": ["内向者", "安静", "性格内向", "高敏感", "社交恐惧"],
    "自卑": ["被讨厌的勇气", "阿德勒", "自卑与超越", "自我接纳", "自信"],
    "讨好": ["讨好型人格", "心理边界", "课题分离", "被讨厌的勇气", "自我接纳"],
    # Parenting & Education
    "家庭教育": ["育儿", "养育", "亲子关系", "亲子", "父母", "教养", "孩子", "parenting"],
    "亲子关系": ["亲子", "家庭教育", "育儿", "养育", "父母", "孩子", "非暴力沟通"],
    "培养孩子": ["育儿", "养育", "家庭教育", "亲子", "自驱力", "教养"],
    "育儿": ["家庭教育", "养育", "亲子关系", "儿童心理", "parenting"],
    # Health, Sleep, Energy & Exercise
    "精力管理": ["精力", "全情投入", "睡眠", "疲劳", "恢复", "时间管理", "the power of full engagement", "energy"],
    "时间管理": ["效率", "精力管理", "自律", "习惯", "番茄工作法", "深度工作"],
    "情绪管理": ["情绪", "情商", "焦虑", "非暴力沟通", "心理调节"],
    "向上管理": ["职场", "汇报", "领导", "沟通"],
    "睡眠质量": ["睡眠", "我们为什么要睡觉", "睡眠革命", "失眠", "昼夜节律", "sleep"],
    "睡眠": ["睡眠革命", "我们为什么要睡觉", "失眠", "健康", "精力", "sleep"],
    "抗衰老": ["衰老", "长寿", "超越百岁", "基因", "细胞", "健康", "营养", "lifespan", "outlive", "aging"],
    "科学锻炼": ["运动", "锻炼", "健身", "跑步", "运动改造大脑", "力量训练", "健康", "exercise", "fitness"],
    "锻炼身体": ["运动", "锻炼", "健身", "运动改造大脑", "体能", "健康", "exercise"],
    "锻炼": ["运动", "健身", "运动改造大脑", "健康", "跑步", "exercise"],
    "健身": ["运动", "锻炼", "力量训练", "减脂", "营养", "fitness"],
    # Communication, Negotiation & Persuasion
    "沟通表达": ["沟通", "非暴力沟通", "关键对话", "演讲", "表达能力", "人际关系", "communication"],
    "表达能力": ["沟通", "结构化思维", "金字塔原理", "演讲", "写作", "口才", "communication"],
    "沟通": ["非暴力沟通", "关键对话", "表达", "倾听", "话术", "人际关系", "communication"],
    "谈判技巧": ["谈判", "优势谈判", "谈判力", "说服", "博弈", "negotiation"],
    "谈判": ["谈判力", "优势谈判", "成交", "说服", "博弈", "negotiation"],
    "说服力": ["说服", "影响力", "先发制人", "修辞", "谈判", "persuasion", "influence"],
    "职场": ["沟通", "向上管理", "晋升", "领导力", "个人成长", "career"],
    # Marketing, Business & Finance
    "营销": ["市场营销", "销售与市场营销", "品牌", "定位", "广告", "推广", "影响力", "说服", "消费者", "marketing", "branding", "positioning", "persuasion"],
    "市场营销": ["营销", "销售与市场营销", "品牌", "定位", "广告", "消费者", "marketing", "branding"],
    "销售": ["销售与市场营销", "谈判", "成交", "客户", "营销", "sales", "selling", "negotiation"],
    "广告": ["营销", "奥格威", "品牌", "文案", "传播", "advertising", "copywriting"],
    "品牌": ["营销", "定位", "传播", "brand", "branding"],
    "创业": ["商业管理与创投", "精益创业", "创新", "商业模式", "风险投资", "startup", "venture"],
    "企业管理": ["商业管理与创投", "管理", "领导力", "组织", "德鲁克", "management", "leadership"],
    "管理": ["商业管理与创投", "领导力", "组织", "德鲁克", "management", "leadership"],
    "投资": ["金融与经济学", "价值投资", "巴菲特", "股票", "资产配置", "investing", "investment"],
    "金融": ["金融与经济学", "货币", "银行", "华尔街", "资本", "finance", "financial"],
    "经济": ["金融与经济学", "经济学", "宏观经济", "微观经济", "博弈论", "economics", "economy"],
    "经济学": ["金融与经济学", "宏观经济", "微观经济", "行为经济学", "economics"],
    # Math, CS, Python, Data & Semiconductors
    "python": ["python编程", "数据分析", "编程入门", "代码", "自动化", "programming"],
    "数据分析": ["数据科学", "统计", "数据挖掘", "商业分析", "python", "data analysis", "data science"],
    "离散数学": ["discrete mathematics", "discrete math", "combinatorics", "具体数学", "组合数学", "数理逻辑", "图论"],
    "集合论": ["set theory", "公理集合论"],
    "数论": ["number theory", "初等数论", "代数数论"],
    "密码学": ["cryptography", "cipher", "现代密码学", "公钥密码"],
    "图论": ["graph theory", "network science", "组合数学"],
    "线性代数": ["linear algebra", "matrix", "矩阵论", "矩阵分析"],
    "微积分": ["calculus", "real analysis", "数学分析"],
    "概率论": ["probability", "stochastic", "数理统计", "随机过程"],
    "统计学": ["statistics", "statistical", "bayesian", "贝叶斯"],
    "机器学习": ["machine learning", "statistical learning", "统计学习", "深度学习"],
    "深度学习": ["deep learning", "neural network", "神经网络"],
    "大语言模型": ["large language model", "llm", "transformer", "generative ai", "生成式ai"],
    "人工智能": ["artificial intelligence", "ai", "智能体", "agent", "机器学习"],
    "ai": ["人工智能", "artificial intelligence", "机器学习"],
    # Dynasties: book titles name eras and figures, rarely the dynasty word itself
    "明朝": ["明史", "明代", "万历", "南明", "朱元璋", "张居正", "永乐", "崇祯"],
    "明代": ["明史", "明朝", "万历", "南明", "朱元璋", "张居正", "永乐", "崇祯"],
    "清朝": ["清史", "清代", "大清", "康熙", "雍正", "乾隆", "晚清"],
    "宋朝": ["宋史", "宋代", "大宋", "北宋", "南宋"],
    "唐朝": ["唐史", "唐代", "大唐", "盛唐"],
    "罗马": ["古罗马", "罗马帝国", "罗马史", "rome", "roman"],
    # Crypto: titles mix 简/繁 and English ('區塊鏈革命', 'The Little Book of Bitcoin')
    "比特币": ["区块链", "區塊鏈", "比特幣", "bitcoin", "blockchain", "加密货币", "数字货币"],
    "区块链": ["區塊鏈", "blockchain", "比特币", "比特幣", "bitcoin", "加密货币", "去中心化"],
    "加密货币": ["比特币", "区块链", "數位貨幣", "数字货币", "crypto", "bitcoin", "blockchain"],
    "数字货币": ["比特币", "区块链", "加密货币", "crypto", "bitcoin", "blockchain"],
    "算法": ["algorithms", "data structures", "数据结构"],
    "芯片": ["芯片战争", "半导体", "光刻机", "集成电路", "台积电", "英伟达", "算力", "chip", "semiconductor"],
    "半导体": ["芯片", "芯片战争", "光刻机", "集成电路", "台积电", "制造", "semiconductor", "chip"],
    "产业竞争": ["芯片战争", "大国博弈", "供应链", "贸易战", "科技战", "工业"],
    "科技博弈": ["芯片战争", "大国博弈", "地缘政治", "人工智能", "半导体", "技术霸权"],
    # History, Dynasties, Fiscal & Bureaucracy
    "古代王朝": ["中国史", "王朝兴衰", "历代王朝", "帝国", "秦汉", "唐宋", "明清", "资治通鉴"],
    "历代王朝": ["中国史", "王朝兴衰", "秦汉", "唐宋", "明清", "帝国统治"],
    "王朝": ["王朝兴衰", "帝国", "中国史", "皇帝", "明朝", "清朝", "唐朝", "秦汉"],
    "财政崩溃": ["财政", "历代经济变革", "赋税", "货币", "王朝兴衰", "大明王朝", "张居正"],
    "财政": ["财政制度", "赋税", "央地关系", "历代经济变革", "金融", "税制"],
    "官场潜规则": ["潜规则", "官场", "官僚体制", "皇权", "政治博弈", "吴思", "血酬定律", "大明王朝"],
    "潜规则": ["官场", "血酬定律", "吴思", "官僚", "权力博弈", "制度"],
    "官场": ["官僚", "政治制度", "皇权", "朝堂", "潜规则", "资治通鉴"],
    "地缘冲突": ["地缘政治", "国际关系", "大国博弈", "战争", "中东", "俄乌", "大棋局", "geopolitics"],
    "地缘政治": ["geopolitics", "国际关系", "大战略", "大国博弈", "地缘"],
    "大国博弈": ["地缘政治", "国际关系", "霸权", "大外交", "战略", "geopolitics"],
    "政治博弈": ["权力博弈", "政治制度", "朝堂", "皇权", "官僚体制", "权谋"],
    "制度演进": ["制度变迁", "国家治理", "财政制度", "央地关系", "历代经济变革"],
    "权谋": ["权术", "帝王术", "资治通鉴", "政治斗争", "权力结构"],
    "历史": ["历史与地缘政治", "中国史", "世界史", "王朝", "帝国", "history"],
    # Philosophy, Existentialism, Suffering & Death
    "人生哲学": ["哲学", "人生意义", "存在主义", "活出生命的意义", "人生的智慧", "叔本华", "尼采", "斯多葛"],
    "存在主义": ["存在主义咖啡馆", "存在与时间", "萨特", "加缪", "海德格尔", "尼采", "虚无主义", "existentialism"],
    "痛苦": ["苦难", "活出生命的意义", "叔本华", "斯多葛", "心理治疗", "死亡", "悲剧"],
    "死亡": ["生死", "最好的告别", "当呼吸化为空气", "生命", "存在主义", "临终", "mortality", "death"],
    "哲学": ["哲学宗教与通识", "形而上学", "伦理学", "存在主义", "philosophy"],
    "逻辑": ["批判性思维", "逻辑学", "逻辑谬误", "critical thinking", "logic"],
    "批判性思维": ["critical thinking", "逻辑谬误", "独立思考", "学会提问"],
    "系统思考": ["systems thinking", "系统动力学", "第五项修炼", "复杂系统"],
    # Design & Product
    "设计": ["产品与UX设计", "用户体验", "交互设计", "设计心理学", "design", "ux"],
    "产品": ["产品与UX设计", "产品经理", "用户体验", "product"],
}

BROAD_PARENT_NOUNS: Set[str] = {
    "数学", "物理", "化学", "生物", "科学", "历史", "历史书", "哲学",
    "心理", "心理学", "经济", "经济学", "金融", "商业", "管理",
    "计算机", "技术", "文学", "艺术", "政治", "社会", "职场", "家庭", "个人", "生活", "工作",
    "理念", "观念", "思路", "方法论",
    "人物", "兴衰", "兴亡", "起源", "演变", "帝国",
    "底层", "原理", "机制", "本质",  # aspect words: '比特币的底层技术' is about 比特币, not books titled 底层逻辑
}

# Tier-2 Target Demographic / Role / Group Modifiers (context qualifiers, not standalone book topics when Tier-1 exists)
TARGET_GROUP_MODIFIERS: Set[str] = {
    "人群", "群体", "人们", "人类", "女性", "男性", "成年人", "年轻人", "青少年",
    "儿童", "孩子", "父母", "家长", "老人", "员工", "领导", "同事", "客户", "读者",
    "女生", "男生", "女人", "男人", "普通人", "现代人",
}

TIER2_CONTEXT_MODIFIERS: Set[str] = BROAD_PARENT_NOUNS | TARGET_GROUP_MODIFIERS

ENGLISH_SENTENCE_MIN_WORDS = 3


