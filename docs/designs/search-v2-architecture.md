# 智能图书馆检索算法 V2 架构设计方案

- **日期**：2026-11-19
- **状态**：五轮评审定稿（2026-11-19，见「〇、评审修订摘要」）/ Phase 0–1 已完成
- **覆盖范围**：书架检索（`GET /api/books?q=...`）、按描述找书（`POST /api/recommend/describe`）、本地 Cross-Encoder（`bge-reranker-v2-m3`）、DeepSeek AI 精排（`POST /api/search/ai_rerank`）及对应词法/向量/数据底座

---

## 〇、评审修订摘要（2026-11-19）

#### 第一轮评审修订（范围收敛与现状核实）

评审时按代码和数据逐条核实了诊断。结论：方向可行，但原方案把几处小改动就能解决的问题，和全文索引重建、双向量、打分重写绑在一起，且可调参数过多。本轮修订如下：

| 项目 | 核实结果 | 修订 |
|---|---|---|
| 「科幻史诗」下 2382 排第 56 | 实测属实（58 条中第 56；2366 第 40，2183 第 15） | 保留 |
| `semantic_query` 重复拼词 | 实测为 `'科幻史诗 科幻史诗'` | 保留，移入第一阶段 |
| `is_anthology` 误标 | 1,577 本中 **974 本 `kind` 非「套装合集」（另有 3 本 `kind` 为空，合计 977 本）**；144 本漏标 | 保留，移入第一阶段；回填前抽样核对 `kind` |
| 打分 `elif` 互斥、半词 52×0.78 | `scoring.py` 属实 | 保留，移入第一阶段 |
| 「≥3 字中文在 FTS5 中 0 命中」 | 夸大：「赫拉利」命中 4 本。实为整段连续中文是一个词元，查询词须等于标点间整段 | 改写 2.2 第 4 条 |
| 「换 BM25F 能修好科幻史诗」 | 不成立：三本书已经由 `LIKE` / AI 关键词路径召回，排低是打分问题 | 全文索引降为第二阶段，按第一阶段结果决定 |
| 书架 `p50 ≤ 250ms` 门禁 | 比现状（73c3a7d 之后 184ms）宽松 | 改为不劣于现状（`≤ 200ms`） |
| 全文索引同步靠各写入点调用 `sync_book_fts` | 漏一处就脱节 | 改为触发器写待同步表（4.1.4） |
| 取缔 `LIKE` | 会丢英文子串（`learn` → `learning`）和单字命中 | 先补这两类用例再决定（4.1.5） |
| View A 双向量 | 收益无证据；仅修去重即可把 2382 从 #396 拉回 #67 | 降为第三阶段，按数据决定 |
| 5-Fold 调参 10+ 个参数 | 175 条评测集上噪声大，易过拟合 | 只调 3–4 个参数，留出集不参与调参（4.4.4） |
| 本地 Cross-Encoder 去留 | 只需一次开/关对比 | 提前到第一阶段 |
| 与进行中的工作 | 工作区有未提交的向量热更新改动（`upsert_book_vector`、`refresh_book_vector_in_memory`、`PASSAGE_SCHEMA_VERSION = 2`） | 第三阶段须在其提交后、以其为基础设计 |

#### 第二轮算法深度评审修订（数学正确性、数值稳定与架构闭环）

| 编号 | 评审发现问题 | 修订方案 |
|---|---|---|
| **C1** | `fts_dirty(book_id PRIMARY KEY)` 无版本号存在并发 TOCTOU 丢更新；迁移建空表首启致稀疏召回归零；读路径调用 `sync_dirty_fts` 违背 ADR-0001 | 新增 `dirty_seq` 乐观锁列 + CAS 删除；严禁读路径写库，改由单后台防抖线程异步同步；新增 `fts_v2_ready` 就绪开关（4.1.4） |
| **C2** | Phase 1 仅补 V1 打分，导致 4.4 归一化融合公式与 BM25F/调参闭环脱节；Phase 1 直接测未补字段的 Cross-Encoder 会假阴性误杀 | Phase 1 先补齐 `bge-reranker` 字段与融合再跑 A/B（同步 4.5.2）；Phase 2 将 BM25F 与 4.4 归一化融合 + `finalize.py` 重标定捆绑落地 |
| **C3** | 4.4.1 BM25F 无分母下界致单条弱噪声得 `1.0` 满分；`dense_search` 的 `top_k*3=480` 截断致 `#481+` 假 `0.0` 悬崖并拉垮 $P_{25}$ | BM25F 加分母下界 $B_{\text{floor}} = 12.0$；RRF 汇聚后对缺失候选补算一次切片点积 `matrix[missing_rows] @ q_vec` 再算 $P_{25}$（4.4.1） |
| **C4** | Phase 4 门禁「留出集指标不低于调参集」在约 35 条（175 条的 20%）留出集样本下统计学不成立 | 修正为：留出集 `nDCG@10` 严格超越同子集 V1 基线（目标 `≥ +0.03`）、泛化间隙 `train - holdout ≤ 0.03`、`Bad@5 = 0`（4.4.4、五） |
| **M1** | 纯 `unicode61` 破坏 `cryptocurrency` ↔ `cryptocurrencies` 等英文复数单测；全边界插 `z0z` 膨胀 BM25 `dl` | 仅在中文段与中文段交界插 `z0z`；英文索引与查询两侧做轻量单复数归一化（4.1.1、4.1.2） |
| **M2** | 短文本 View A 余弦基线比 View B 高 `0.03–0.07`，直接取 `max` 令长查询退化为 View A 霸权 | 融合前先在候选池内做视图内稳健中心化对齐，同时保留原始最大余弦用于 `≥ 0.54` 阈值（4.2.4） |
| **M3** | 4.4.3 将语义救援盲降至 `0.50` 与 `finalize.py` 硬门槛失配 | 保持语义救援门槛在原始余弦 `≥ 0.54`，Phase 2 联动重标定 `finalize.py` 阈值（4.4.3） |
| **M4** | 未标明复杂度与 100× 数据扩展天花板 | 第六章新增复杂度分析与 `≤ 20 万册` 架构上限说明（超限需切换 HNSW/IVF） |
| **M5** | 文档存在 `974 vs 977`、`≤ 250ms vs ≤ 200ms`、4.5.2 阶段编号 3 处不一致 | 全文统一口径与指标门禁（一、二、4.3、4.5.2、五） |

#### 第三轮评审修订（修法本身的缺陷）

| 编号 | 发现问题 | 修订方案 |
|---|---|---|
| **R1** | C1 的「写入口提交后唤醒同步线程」唤不醒跨进程写入：`cli.py organize`、`enrich_ai.py`、`sync_display_titles.py` 都是独立进程，「100ms 防抖」对它们无效 | 去掉唤醒；后台线程每 2–5 秒轮询 `SELECT 1 FROM fts_dirty LIMIT 1`，写入点完全不知道同步存在；新书在全文召回中最多晚几秒出现（4.1.4） |
| **R2** | C4 新门禁仍小于噪声：`baseline.json` 实测逐查询 nDCG 标准差 `0.23`，35 条留出集均值标准误约 `0.039`，`+0.03` 不到 1 个标准误；「调参集与留出集之差 ≤ 0.03」是两均值之差，噪声更大 | 主门禁改为全量 175 条逐查询配对自助法：nDCG 增量 95% 置信区间下限 `> 0`；各查询类型均值不下降；`Bad@5 = 0`。留出集只作过拟合观察，不设硬阈值（4.4.4、五） |
| **R3** | C3 的 $B_{\text{floor}} = 12.0$ 无依据：bm25 原始分量级取决于字段权重与语料 | 全文索引建成后，用评测集各查询第一名 bm25 原始分分布标定一次（如中位数 × 0.3），之后作结构性常量，不参与调参（4.4.1） |
| **R4** | C2 让 Phase 1 新增「有界融合」权重，且在 175 条上看结果定参，与防过拟合规则冲突 | Phase 1 只补 Cross-Encoder 输入字段并把 `max_length` 放到 `160`，保留现有 `(rr_prob - 0.15) * 75.0` 融合后直接做开/关对比；融合公式随 Phase 2 归一化一起改（4.5.2、五） |
| **R5** | M1 自写英文单复数归一化，索引与查询两侧易不一致 | 改用 FTS5 内置 `tokenize='porter unicode61'`，两侧自动一致，顺带让 `learning` 命中 `learn`；其范围比 `_has_term` 宽（`running` → `run`），须用测试用例固定全文召回与打分对「命中」的差异（4.1.1–4.1.3） |
| **R6** | $L_i$ 中 bm25 与分档词法证据来自同一批字段命中，线性混合会重复计分，$\gamma$ 难以解释 | 明确分工：$\tilde{b}_i$ 只表达「多词、哪些字段、出现多少」；$\tilde{\tau}_i$ 只表达「完整词还是半词」。若实测重复计分明显，改为 $\tilde{\tau}_i$ 只作门槛与封顶，不参与线性混合（4.4.3） |

#### 第四轮评审收口修订（统计无偏、延迟可达与落地边界）

| 编号 | 实测发现问题 | 收口方案 |
|---|---|---|
| **C1'** | `Top-12, max_length=160` 实测重排耗时 `~1,235ms`，叠加 Stage-1 `~190ms` 端到端约 `1,425ms`，与 `p50 ≤ 1200ms` 物理互斥；且 4.5.2 与 Q7 仍残留「Phase 1 改有界融合」文本矛盾 | 选定方案 A：对齐现状基线（`1364ms`）将 `describe` 开重排门禁放宽为 `p50 ≤ 1500ms`（注：`Top-8, max_length=128` 实测 `926ms`，作为硬限 `≤ 1200ms` 备选）；同步清理 Q7 与 4.5.2 矛盾，明确 Phase 1 仅补字段与 `max_length=160` 并保留 `(rr_prob - 0.15) * 75.0`，有界融合随 Phase 2 落地 |
| **C2'** | 全量 175 条配对自助法混入了 80%（140 条）调参集样本内拟合结果，存在样本内乐观偏差 | 改为 **175 条严格样本外配对自助法（140 条 5-Fold 折外 OOF 预测 + 35 条留出集预测）**，要求 95% CI 下限 `> 0`，并单独汇报 74 条人工校验 `gate` 子集（4.4.4、五） |
| **C3'** | `4.4.4` 误写了不存在的类型 `'generated'`，且对 14 条小类（`SE` 达 `0.05–0.08`）设零容忍必触发假拒绝 | 按 `baseline.json` 修正为真实 8 类（`author / english / known_item / negation / nl / style / topic / traditional`），单类守卫放宽为「均值降幅 `≤ 0.015` 且该类配对 95% CI 不显著为负」（4.4.4、五） |
| **R3'** | 补充 `$B_{\text{floor}}$` 标定数据隔离边界 | `$B_{\text{floor}}$` 仅使用 80% 调参集首名 bm25 原始分布标定，严禁接触 20% 留出集（4.4.1） |
| **M1'** | 批量后 `fts_dirty` 积压数千行会造成单事务写锁饥饿；触发器伪代码误对 `book_ai_meta` 与 `DELETE` 用 `new.id` | 强制 `sync_dirty_fts` 按 `LIMIT 200` 分批提交并 batch 间让出写锁；触发器区分 `books(new.id/old.id)` 与 `book_ai_meta(new.book_id/old.book_id)`；同进程写入可选触发 `event.set()` 零耦合唤醒（4.1.4） |
| **M2'** | 本机 SQLite `porter` 会把中文拼音当英文截断（`shijing→shije`、`beijing→beij`、`wenming→wenm`），污染首字母命中 | `books_search_fts.aux_tok` **只存 `isbn`**，移除拼音（拼音已由 4.1.5 专用路径独立覆盖）（4.1.3） |
| **M3'** | R6「降级为仅作封顶」表述容易丢失 BM25F 无法表达的多概念覆盖、`is_disjunctive` 析取与多义词消歧语义 | 明确正交边界：$\tilde{b}_i$ 管单概念 TF/IDF 与字段权重，$\tilde{\tau}_i$ 负责 Tier A/B 封顶 + 多概念覆盖 / 析取 / Tier-2 与多义消歧调节（4.4.3） |
| **M4'** | `traditional` 是全库 8 类最低分（均分 `0.5275`，库内有 `1,630` 本繁体藏书），此前未覆盖 | 在词法索引与查询解析侧引入轻量繁→简字符映射折叠（仅作检索键，不改展示书名），兼顾繁体/简体交叉检索（4.1.1、4.1.2、Phase 1/2） |

#### 第五轮评审定稿（门禁统计与 Phase 1 可落地性）

| 编号 | 发现问题 | 定稿方案 |
|---|---|---|
| **F1** | C3' 单类守卫「降幅 `≤ 0.015`」小于各类标准误（`0.026–0.075`）的 1/3，随机波动即越线；8 类各做 95% 检验存在多重比较 | 删除 `0.015`；单类守卫只保留「配对置信区间不显著为负」，按 8 次比较做 Bonferroni 校正（99.4% 区间）（4.4.4、五） |
| **F2** | M4' 在 Phase 1 做繁简折叠无法落地：Phase 1 词法仍在原文上做 `LIKE` / 子串匹配，只折叠查询会让繁体书失配，两侧折叠需要折叠副本（即 Phase 2 索引） | Phase 1 改为**查询侧扩展**：保留原词，并把简体形式作为同义词加入；索引两侧折叠随 Phase 2。转换用系统 ICU `Traditional-Simplified`（经原生库 `NSString applyingTransform`），手写表仅作原生库不可用时兜底（4.1.1、五） |
| **F3** | 离线重建与后台同步是两条写入路径，可能并发写同一张表 | `rebuild_search_fts` 只把全部活跃书写入 `fts_dirty`，再调用同一个 `sync_dirty_fts` 清空；`fts_dirty` 清空后置 `fts_v2_ready = 1`（4.1.4） |
| **F4** | 140 条 OOF 来自 5 组折内参数，不是上线的平均参数 | 门禁仍用 175 条样本外拼接；另报告**上线参数**在 35 条留出集上的结果，作为上线版本的真正样本外估计（4.4.4） |
| **F5** | `event.set()` 需写入点主动调用，并非「零耦合」；M2' 例子中 `shijing` 实测词干为 `shije`（原文写作 `shij`，已更正）；Q2 与第三章流程图残留「0 命中」「取缔慢 LIKE」旧说法；`traditional` 14 条均为生成查询，仅 5 条人工确认 | 措辞更正；`traditional` 结论注明标注来源 |

#### Phase 1 实施记录（2026-11-19）

- **合集口径按抽样结果修正**：抽样 30 本「按 `kind` 口径将取消合集标记」的书，约 13 本是真套装（`奥威尔小说全集（套装共6册）`、`管理的艺术【套装五册】`、`阿提卡之夜套装（全4册）`）。`kind` 描述的是内容体裁，不是包装形式，不能单独作为主口径。实施规则改为：**`kind == '套装合集'` 或 书名/副标题命中去掉「丛书」后的正则**，即视为合集。按此规则抽样 25 本取消标记的书，全部为出版社丛书中的单行本。结果：合集由 `1,577` 本变为 `1,273` 本（取消 `448` 本，新增 `144` 本）。2382 仍为合集，其副标题为「1-15大全集」，本就是 15 册套装。
- **繁体查询**：用系统 ICU `Traditional-Simplified`（Python 经 `ctypes` 调 CoreFoundation 的 `CFStringTransform`，无需重编原生库）把查询转为简体后再做意图分析，原繁体写法作为同义词保留。原因：停用词与词典都是简体，`關於商業策略與創新的書籍` 直接分析会把「關於」「書籍」当成主题词。
- **繁体切片补标注**：首轮评测中 V2 的繁体查询前 10 有 68% 是未标注书（V1 为 5%），`traditional` 切片 −0.12 是缺标注造成的假象。用 `eval_build.py` 的 `label_one` 对 14 条繁体查询补标注 248 本后重评：书架 `traditional` +0.281（99.4% CI +0.116..+0.447），描述 +0.317。
- **评测结果（V1 → V2，175 条，配对自助法）**：书架 nDCG@10 +0.027（95% CI +0.010..+0.045），gate 74 条 +0.020（CI 含 0）；描述 +0.034（+0.016..+0.054），gate +0.017（CI 含 0）；8 类均无显著负向，`Bad@5 = 0`。`科幻史诗` 下 2382/2366/2183 排第 1/4/3（原 56/40/15）。
- **描述模式重排默认值**：`Top-12, max_length=160` 实测 `p50 = 1940ms`，超出 `≤ 1500ms` 门禁。A/B（同为 V2 代码、串行跑）：关闭 Cross-Encoder 降至 `206ms`，但对 V1 的 `english` 显著负向（−0.077）、整体 +0.010 不显著，不选；`Top-8, max_length=128` 降至 `992ms`，对 V1 整体 +0.028（95% CI +0.012..+0.046），8 类均无显著负向。默认改为 `RERANK_TOP_N = 8`、`RERANK_MAX_TOKENS = 128`。
- **重排默认开启的门禁依据**：按 4.5.2 的开/关对比口径，`Top-8, max_length=128` 相对关闭 Cross-Encoder 的 gate 74 条 nDCG@10 +0.018（95% CI +0.006..+0.032），下限 `> 0`，且 `p50 = 992ms ≤ 1500ms`，满足保留默认开启的条件。
- **按 `search-v2-phase1-review.md` 的修补**（2026-11-19）：
  - gate 隔离：撤回写入 5 条 `verified` 繁体查询的 98 条 DeepSeek 标注，gate 只保留人工标注；非 gate 查询的补标保留。
  - 繁体：变体前插到 `synonym_terms` 最前（调用方只读前 6 个）；大小写不敏感定位；书名精确匹配同时查简繁两种写法（`explicit_title_variant`），书名置顶比较在简体形式下进行；排除词补繁体写法。`hanzi.py` 捕获库加载异常，`CFStringCreateMutableCopy` 纳入 `try`，`CFRelease.restype = None`，含 NUL 的文本原样返回。
  - Tier B 半词：只对纯中文 3–4 字词生效；双半词要求两半都通过泛词过滤；标题命中任一半优先于简介命中（拆为 `search/term_points.py`）。
  - `build_semantic_query` 最多追加 3 个扩展词。`rerank_document` 对空字段与非字符串关键词做防御；AI 元数据随候选项传递（`_ai_meta`），`rerank_and_fuse_candidates` 去掉新增的第 6 个参数。
  - AI 字段：`subfield` 并入 meta 档、`clean_title` 并入标题文本。`core_points` / `target_audience` 按 28 分计分会拉低质量（消融：去掉后书架 gate +0.007，95% CI +0.0007..+0.0135；全量 +0.009），**不纳入打分**。
  - 合集释放的下游影响：取消标记的 448 本解除兴趣推荐屏蔽（多为哲学宗教 94、个人成长 78、数学 64、历史 55、社科 45）；外部评分抓取队列不受影响（两批书均已有 `book_external_meta`，队列为 0）。
  - 未采纳：`_analyze_query_intent` 改名属于原有 330 行豁免函数的平移，拆分不在 Phase 1 范围；繁→简手写兜底表不做（服务只在 macOS 运行，CoreFoundation 恒在，且库加载失败时已降级为原文）；`is_anthology_book` 返回 0/1 以对齐 SQLite 列。
  - 修补后同数据对比（HEAD → 修补，175 条）：书架 nDCG@10 +0.006（CI 含 0），gate −0.007（CI 含 0）；描述 +0.006（CI 含 0），gate −0.004（CI 含 0）；8 类均无显著负向，`Bad@5 = 0`。
  - 环境说明：`data/bge_m3_vectors.npy` 于 22:12 被重建（晚于 `306bb8b`），HEAD 代码在当前数据与纯人工 gate 标注下书架 gate 为 0.6831，已低于 `test_search_quality` 阈值 0.6835。
  - `toc_text` 广告过滤：向量热更新改动已提交（`09e04bd`），在 `build_book_passage` 中按条目剔除盗版推广行（`TOC_AD_LINE`，保留「微信打败米聊」这类正文章节）。全库命中 4 本（2382、2769、9810、19186，整份目录均为广告），增量重编码 4 本，`科幻史诗` 下 2382/2366/2183 仍排第 1/4/3。
- **收尾（2026-11-19 晚）**：
  - **ICU 误转修复**：`Traditional-Simplified` 会把 18 个 GB2312 字改写，其中 11 个是规范简体（`俱乐部 → 具乐部`、`著作`、`乾坤`、`大阪`、`哪吒`、`摔跤`）。`to_simplified` 改为只转 GB2312 以外的字，另加 7 个现代只作繁体用的 GB2312 字（`後於麽夥徵菸摺`）。此前「那本讲秘密俱乐部操控世界的书」因此丢失 1681（V1 第 1 名），修复后回到第 1。
  - **Phase 0 留出集**：`tests/search_eval/holdout_queries.json` 14 条（科幻史诗、心理史学、量子计算、英文单复数/子串、单字「禅」「茶」、简繁交叉、繁体书名置顶、泛词半词），只由 `eval_search.py --holdout` 运行，不进 gate 与调参；标签为正则草稿，需人工复核。V1 0.664 → 当前 0.762（书架，+0.098，n=14，CI 含 0）。暴露的弱项：`startups` 复数（0.469，单数 0.870）、`AI時代的學習` 中「學習」被当泛词丢弃（0.000）。
  - **V1 快照**：`tests/search_eval/v1_snapshot.json`（41aa67b 代码 + 回填前数据库，向量为 22:12 重建后的版本，仅指标）。gate 繁体补标核对后对比当前：书架 nDCG@10 +0.036（95% CI +0.017..+0.057），gate +0.025（CI +0.0003..+0.056）；描述 +0.034（+0.016..+0.054），gate +0.020（CI 含 0）；`traditional` 两模式均显著为正（书架 +0.298，描述 +0.291），8 类均无显著负向，`Bad@5 = 0`。
  - **gate 繁体补标**：5 条已核实繁体查询的人工标注只覆盖 V1 的结果池，V2 新召回的繁体书曾一律按 0 分计。撤回的 98 条 DeepSeek 判定经人工核对（改判 4 条：gen-05-08 的 11238 → 2；gen-13-05 的 18809/18810/18811 → 1）后并回 gate。`nl-wwii` 的正则标签仍只认简体「二战」。
  - **测试副作用修复**：`test_server_routes_characterization` 的摘要编辑会调度真实目录回写，定时线程随测试进程退出而中断，在仓库根目录留下 `.电子书分类索引与简介目录.xlsx.<pid>.tmp`；该测试已 mock `catalog_sync.enqueue_catalog_sync`。

---

## 一、决策对齐记录（Q1–Q8）

| 编号 | 决策主题 | 已确认方案 | 核心理由与取舍 |
|---|---|---|---|
| **Q1** | 升级定位与延迟预算 | **分阶段本地检索改进**（书架 `p50` 不劣于现状，即 `≤ 200ms`；描述找书开重排 `≤ 1500ms`，关重排 `≤ 200ms`） | 把无 AI 时的本地检索质量拉近 LLM 水平，同时为 AI 精排输送更准候选池 |
| **Q2** | 词法召回层（Sparse） | **字段级位置 Bigram + 词元 FTS5（单表 BM25F，含繁简折叠与 `porter`）** | 零新依赖，解决整段中文作为单一词元、子串无法命中的问题，索引 `book_ai_meta` 与 `target_audience`，按前置验证结果取代慢 `LIKE` |
| **Q3** | 向量表示层（Dense） | **双视角多向量索引**（元数据短向量 View A + 内容长向量 View B，视图对齐后自适应 MaxSim）+ 修复 `semantic_query` 重复拼词与广告目录过滤 | 兼顾短概念不被长简介稀释与长句剧情匹配，查询仅增 `~2.5ms`，短向量全库构建仅需数分钟 |
| **Q4** | 粗排融合与打分范式 | **查询内归一化混合融合 + 严格分层词法证据**，并在 175 条基准集上做 5-Fold 交叉验证校准 | 消除跨查询分数尺度漂移与魔数耦合，保证可解释性与稳定泛化 |
| **Q5** | 合集惩罚与数据卫生 | **以 `book_ai_meta.kind == '套装合集'` 或去掉「丛书」后的书名正则命中修正 `is_anthology` 并回修 DB**（实施时按抽样改为两者取或，见 Phase 1 实施记录）；轻度降权（`-8 ~ -12`） | 解除对 **974 本 `kind` 非「套装合集」（另有 3 本 `kind` 为空，合计 977 本，占原标记 62%）** 的单部名著/单系列全集误伤与 `-24` 重罚，释放回推荐与评分队列 |
| **Q6** | 复合词拆干策略 | **严格分层 + 双半词协同**：完整词多字段饱和叠加居高优；2 字拆干仅作低权兜底，双半词协同明显高于单边半词 | 保证「单边沾半词」在数学上永不反超「完整词命中」 |
| **Q7** | 精排层协同 | **DeepSeek 精排与本地 Cross-Encoder 同步补齐 AI 字段 + DeepSeek 扩窗 20→30**；本地 `bge-reranker-v2-m3` 于 Phase 1 补齐字段，实测后定为 `Top-8, max_length=128`（保留旧融合公式）并按门禁保留默认开启，有界融合随 Phase 2 落地 | 避免拿残缺旧集成（104 token/缺字段）做 A/B 产生假阴性，也不提前新增融合参数 |
| **Q8** | 验收门禁与执行方式 | **先落盘设计文档审定**；实施时按 Phase 0–4 分阶段推进并执行严格备份与量化门禁 | 降低大规模索引重建与排序重写风险，每阶段独立可验证、可回滚 |

---

## 二、问题诊断与现状基线

### 2.1 量化基线（实测 175 条基准集）

| 评估维度 | 当前现状（V1） | V2 目标 |
|---|---|---|
| 书架本地检索 (`shelf`) | 全量 `nDCG@10 = 0.6721`（gate 74 条 `0.7035`），`P@5 = 0.7269`，`Hit@3 = 0.8114`，`Bad@5 = 0`，`p50 = 190ms`（全量 `210ms`） | gate `nDCG@10 ≥ 0.76`，`Hit@3 ≥ 0.88`，`Bad@5 = 0`，`p50 ≤ 200ms`（73c3a7d 合并 `LIKE` 扫描后实测 `184ms`） |
| 按描述找书 (`describe`) | 全量 `nDCG@10 = 0.6861`（gate 74 条 `0.7015`），`Hit@3 = 0.84`，`Bad@5 = 0`，`p50 = 1364ms`（全量 `1369ms`） | gate `nDCG@10` 显著优于 V1；开本地重排时 `p50 ≤ 1500ms`（若本地重排无正收益关闭后则降至 `≤ 200ms`） |
| AI 精排上限 (`baseline_ai`) | `shelf` 全量 `nDCG@10 = 0.8636`（gate 54 条 `0.8387`，`Hit@3 = 1.0`）；仅排粗排前 20 且缺 `keywords/core_points` | 提高粗排 Top-30 召回密度并补全 AI 字段，缩小离线检索与在线 LLM 之间约 `0.19` 的 `nDCG` 鸿沟 |

### 2.2 典型案例根因解剖（查询「科幻史诗」）

检索 `科幻史诗` 时，已有新提炼简介与 AI 关键词「科幻史诗」的《银河帝国 (Foundation)》（`id=2382`）在 `GET /api/books` 仅排第 **56 / 58 名**（`77.80` 分 vs 第 1 名 `122.61` 分），由以下 4 项结构性缺陷叠加导致：

1. **词法权重倒挂**：`scoring.py` 中 `summary`（`36.0 × w_pt = 63.68`）与 `ai_keywords` 处于互斥 `elif` 链无法叠加；而全文不含「科幻史诗」的书落入 2 字拆干分支，标题沾半个词 `科幻` 或 `史诗` 即可拿 `52.0 × 0.78 × w_pt = 71.75` 分，导致 55 本仅含半词的书集体压过全库仅有的 3 本完整命中书（`id=2183/2366/2382`）。
2. **`is_anthology` 误伤**：`classic_scorer.py` 正则包含 `丛书`，导致全库 `1,577` 本 `is_anthology=1` 中有 **`974` 本 `kind` 非「套装合集」（另有 `3` 本 `kind` 为空，合计 `977` 本，占 62%）** 被误标（`book_ai_meta.kind` 实为学术理论/小说文学等，包含《物种起源》《理想国》《君主论》《银河帝国》），搜索直扣 `-24.0` 分且 `classic_score` 被打 `0.68` 折；另有 `144` 本真套装漏标。
3. **`semantic_query` 重复拼词**：`intent.py` 将 `clean_pos_sentence`（`"科幻史诗"`）与 `primary_terms`（`["科幻史诗"]`）未去重直接拼接成 `"科幻史诗 科幻史诗"` 喂给 BGE-M3，导致 `id=2382` 的余弦相似度从 `0.4865`（全库向量 **#67**）跌至 `0.4026`（全库 **#396**，`dense_pts` 从 `14.33` 跌至 `5.52`）；同时 20 本书的 `toc_text` 含公众号引流广告污染向量。
4. **FTS5 与精排协同断层**：现有 `books_fts` 占 `166MB`，使用 `unicode61`，一整段连续中文是一个词元，查询词只有恰好等于标点之间的整段时才命中（「赫拉利」命中 4 本，「科幻史诗」0 本）；且未索引 `book_ai_meta` 和 `target_audience`。注意：本例中三本书已由 `LIKE` / AI 关键词路径召回，排名低的直接原因是第 1–3 条，不是本条；`frontend/assets/js/api.js` 的「AI 精排」只取粗排前 20 本且 `ai_search.py` 未传入 `keywords`、`core_points`、`subfield`。

---

## 三、V2 整体检索管线架构

```text
Query
 │
 ├─► Stage 1: Intent 意图解析 (backend/search/intent.py)
 │     ├─ 意图分类: EXACT_ENTITY / SHORT_TOPIC / NL_DESCRIBE
 │     ├─ 修复 semantic_query 去重（消除 "词A 词A" 重复塌陷）
 │     └─ 输出 primary_terms (IDF 加权) / synonym_terms / negative_terms / style_mode
 │
 ├─► Stage 2: 双路高覆盖召回 (backend/search/recall.py)
 │     ├─ [Sparse]  books_search_fts 位置 Bigram+词元 BM25F (Top-180, ~2-5ms)
 │     │            + 精确书名 / 作者 / ISBN / 拼音索引快速通道（满足 4.1.5 前置条件后取缔慢 LIKE 全表扫描）
 │     └─ [Dense]   BGE-M3 双视角向量矩阵 (View A 元数据短向量 + View B 内容向量)
 │                  查询自适应 MaxSim 融合 (Top-180, ~2.5ms)
 │
 ├─► Stage 3: RRF 候选汇聚 → Top-180 候选池
 │
 ├─► Stage 4: 查询内归一化混合打分 (backend/search/scoring.py v2)
 │     ├─ 查询内分布校准: log-归一化 BM25F + 稳健下界保护 Dense 归一化
 │     ├─ 严格分层词法证据: Tier A 完整词多字段饱和叠加 >> Tier B 双半词协同 > 单边半词
 │     ├─ 意图自适应配权 + 轻量化真实套装合集惩罚 (kind='套装合集')
 │     └─ 硬护栏: 精确书名/作者置顶、否定词惩罚、难度风格匹配、5-Fold CV 校准系数
 │
 └─► Stage 5: 精排协同与去重截断 (backend/search/finalize.py, backend/ai_search.py)
       ├─ [离线 describe] 补齐 AI 字段后的 bge-reranker-v2-m3（按实测门禁决定默认去留）
       └─ [在线 AI 精排]  窗口 20→30，补齐 subfield / keywords / core_points 喂给 DeepSeek
```

---

## 四、核心模块算法详设

### 4.1 词法召回层：字段级位置 Bigram + 词元 FTS5（BM25F）

#### 1. 统一分词器 `tokenize_for_bm25f(text: str) -> str`
纯 Python 标准库实现，索引构建与查询改写共用同一分词规则，零第三方依赖：
- **繁→简检索键折叠（Phase 2 索引两侧；Phase 1 只做查询侧扩展）**：转换使用系统 ICU `Traditional-Simplified`（经现有原生库 `libmacbookai.m` 调用 `NSString applyingTransform`，零新依赖、覆盖完整），手写映射表仅在原生库不可用时兜底。**仅作检索键，绝不修改数据库与前端展示书名**。Phase 1 因词法仍在原文上匹配，改为查询侧扩展：实施时改为先转简体做意图分析（停用词与词典都是简体），再把繁体原词置于同义词最前，并为书名精确匹配与排除词保留繁体写法；简体书靠简体词命中，繁体书靠原词命中。评测中 `traditional` 类 14 条均为生成查询，仅 5 条人工确认，均分 `0.5275` 结论需带此说明。
- **分段与最小化防跨句哨兵**：按标点、空白和字段边界切分。**仅在相邻“中文结尾段”与“中文开头段”之间插入哨兵词元 `z0z`**（防止关键词列表、章节或字段边界拼出虚假跨段 2-gram）；英文/数字/无汉字相邻边界不插 `z0z`，避免长目录、多标签文档的 BM25 文档长度 `dl` 虚增 20–40% 而扭曲长度归一化。
- **中文连续串（长度 $L \ge 2$）**：输出相邻重叠 2-gram（`s[i:i+2]`），例如 `科幻史诗` → `科幻 幻史 史诗`（相邻位置 $p, p+1, p+2$）。长度为 1 的孤立汉字保留原字。
- **英文与数字串**：转小写保留完整词元；特殊技术标识符（如 `c++` → `cpp`、`c#` → `csharp`、`f#` → `fsharp`、`node.js` → `nodejs`）在索引与查询两侧同构映射。单复数与词形交给 FTS5 内置的 `porter` 词干化（见下方 Schema），不自写归一化规则：`cryptocurrencies` / `cryptocurrency`、`startups` / `startup`、`learning` / `learn` 在索引与查询两侧自动一致。注意 `porter` 比 `search/lexicon.py` 的 `_has_term` 宽（`running` → `run`），全文召回与 Tier 打分对「命中」的定义因此略有不同，须用单元测试固定。

#### 2. 查询表达式转换 `term_to_fts_expr(term: str) -> str`
- 查询词先经过与索引同构的繁→简检索键折叠（并保留原词兼容 V1 路径）；
- 2 字中文 `科幻` → `"科幻"`；
- ≥3 字中文 `科幻史诗` → FTS5 位置短语 `"科幻 幻史 史诗"`（FTS5 严格要求 `科幻` 紧邻 `幻史` 紧邻 `史诗`，数学上等价于连续子串精确匹配，兼具 C 级倒排速度与字段 BM25 得分）；
- 英文词 `foundation` / `cryptocurrencies` → 原样输出 `"foundation"` / `"cryptocurrencies"`，由 `porter` 词干化在 FTS5 内部与索引侧对齐；
- 中英混合词（如 `python量化`）按字符类型拆段，输出 `("python" "量化")`（相邻或同文档 `AND` 组合）；
- 对主概念词（`primary_terms`）与同义词（`synonym_terms`）构建组合 FTS 查询，同时对 3–4 字非词典复合词可选追加降权 `OR` 半词兜底分支，避免唯一主词召回过窄。

#### 3. 单表 7 列字段权重 Schema（`books_search_fts`）

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS books_search_fts USING fts5(
    title_tok,   -- clean_title + title + subtitle + series            (BM25 weight 12.0)
    author_tok,  -- author + translator                                (BM25 weight  9.0)
    meta_tok,    -- ai_category + category + subfield + tags + keywords (weight 7.0)
    summary_tok, -- summary                                            (BM25 weight  4.0)
    points_tok,  -- core_points + target_audience                      (BM25 weight  3.5)
    toc_tok,     -- 清洗广告后的 toc_text                               (BM25 weight  2.0)
    aux_tok,     -- isbn（不存拼音；拼音由 4.1.5 专用路径覆盖）           (BM25 weight  3.0)
    tokenize='porter unicode61'
);
```

- 说明：`books_search_fts` 采用独立内容存储，确保可在单个 FTS 表内聚合 `books` 与 `book_ai_meta` 两张表的字段（`clean_title`、`keywords`、`ai_category`、`subfield`）。**`aux_tok` 只存 `isbn`**：SQLite 实测 `porter` 会把中文拼音当作英文词干截断（`shijing` → `shije`、`beijing` → `beij`、`wenming` → `wenm`），导致全拼与首字母缩写碰撞，且拼音已由 4.1.5 的专用拼音检索路径覆盖。
- 检索取回稀疏分：`raw_bm25 = -bm25(books_search_fts, 12.0, 9.0, 7.0, 4.0, 3.5, 2.0, 3.0)`（因 SQLite FTS5 `bm25()` 返回负值，取反后越大表示相关度越高），作为连续信号直接传入 Stage 4。

#### 4. 一致性同步（CAS 乐观锁 + 分批后台同步 + 就绪开关）与旧表瘦身
- **带版本号待同步表与 SQL 触发器**：在 `books`（字段白名单 `WHEN` 守卫）与 `book_ai_meta` 上分别建纯 SQL 触发器（注意 `books` 主键为 `id`，`book_ai_meta` 主键为 `book_id`，`DELETE` 触发器取 `old.id` / `old.book_id`），写入带递增版本号 `dirty_seq` 的待同步队列（新增写入点也不会漏，且避免主键冲突吞更新）：
  ```sql
  CREATE TABLE IF NOT EXISTS fts_dirty (
      book_id   INTEGER PRIMARY KEY,
      dirty_seq INTEGER NOT NULL DEFAULT 1
  );
  -- books INSERT / UPDATE 触发器用 new.id；DELETE 触发器用 old.id：
  INSERT INTO fts_dirty(book_id, dirty_seq) VALUES (new.id, 1)
  ON CONFLICT(book_id) DO UPDATE SET dirty_seq = dirty_seq + 1;

  -- book_ai_meta INSERT / UPDATE 触发器用 new.book_id；DELETE 触发器用 old.book_id：
  INSERT INTO fts_dirty(book_id, dirty_seq) VALUES (new.book_id, 1)
  ON CONFLICT(book_id) DO UPDATE SET dirty_seq = dirty_seq + 1;
  ```
- **严格遵守 ADR-0001，严禁读路径写库**：绝不在 `GET /api/books` 等只读检索请求路径内调用 `sync_dirty_fts` 或获取写锁（避免与 `enrich_ai.py` / `scheduler` 争抢 SQLite 单写锁引发 `SQLITE_BUSY`，也避免在 `?mode=ro` 只读连接/单测下报错）。
- **后台单线程轮询 + 分批限流（`LIMIT 200`）+ CAS 清理**：`backend/search/fts_index.py` 提供 `sync_dirty_fts(conn_factory, batch_size=200)`，由服务进程内的单一后台线程每 2–5 秒轮询一次 `SELECT 1 FROM fts_dirty LIMIT 1`（同时允许服务内交互写路径如前端「AI 重新提炼」顺手触发一次可选的 `event.set()` 唤醒（需写入点主动调用）以把响应压至 `< 200ms`，而 `cli.py organize` / `enrich_ai.py` 等跨进程写入完全无需感知、由轮询自动兜底）。每次最多处理 **`LIMIT 200` 本**，批间短暂让出写锁，避免批量 `enrich_ai.py` 写入数千本时单次 `write_tx()` 长期霸占写锁饿死前台操作。每批严格遵守 ADR-0001 短事务协议：「短读 `LIMIT 200` 的 `(book_id, dirty_seq)` 及行快照 → 立即关闭读连接 → Python 分词计算 → `write_tx()` 内重写 `books_search_fts` 并执行 CAS 删除 `DELETE FROM fts_dirty WHERE book_id = ? AND dirty_seq = ?`」。若分词期间发生并发编辑，`dirty_seq` 已被触发器递增，CAS 删除受影响行数为 `0`，下一轮自动补同步。
- **迁移安全与 `fts_v2_ready` 就绪门禁**：服务启动迁移只建空表、`fts_dirty` 与触发器，并将元数据开关 `fts_v2_ready` 置 `0`。全量重建 `rebuild_search_fts(conn)` 作为独立离线命令执行，完成后原子将 `fts_v2_ready` 置 `1`；`recall.py` 仅在 `fts_v2_ready == 1` 时切入 `books_search_fts`，此前自动回退 V1 路径，**杜绝迁移后首启空表导致稀疏召回归零**。
- **单一写入路径**：`rebuild_search_fts` 不直接写 `books_search_fts`，只把全部活跃书编号写入 `fts_dirty`，再调用同一个 `sync_dirty_fts` 清空队列；`fts_dirty` 清空后才置 `fts_v2_ready = 1`。重建与后台同步因此共用 CAS 逻辑，不会并发写出两份结果。
- **删除清理与旧表瘦身**：软删除（`is_deleted=1`）与物理删除同样经触发器进入 `fts_dirty`，同步时删除对应行，避免幽灵行残留。待 Phase 2 全量验证通过后，可在后续清理阶段剥离旧 `books_fts` 中 `166MB` 的冗余 `pinyin_search` 索引数据（预计净减 DB 体积 `60–90MB`）。

#### 5. 取缔慢 `LIKE` 全表扫描（有前置条件）
- 前置条件：先在评测集补齐 `LIKE` 能命中、整词全文索引会漏的两类用例——英文子串（如 `learn` 命中 `learning`）与单个汉字查询；新召回在这些用例上不退化才移除。73c3a7d 之后 `LIKE` 已合并为一次扫描（约 40ms），移除带来的耗时收益有限，主要收益是按 BM25 排序代替按 `quality_score` 截断。
- `backend/search/recall.py` 中移除 `_direct_hits_in_one_scan` 对通用主题词的全表 7 字段 `LIKE` 扫描（消除每轮 `~40ms` 全表扫开销与 `LIMIT 80` 截断漏洞），仅保留：
  1. 精确书名 / `norm_title` 索引定位（保障精确搜书名绝对进入候选池）；
  2. 纯拼音查询路径；
  3. 经典分类兜底查询（`wants_classic` 且命中 `inferred_categories`）。

---

### 4.2 向量表示层：双视角多向量 + Query 净化 + 广告目录清洗

#### 1. 修复 `semantic_query` 重复拼词（`backend/search/intent.py`）
- 当前 `sem_parts = [clean_pos_sentence]` 会再无条件追加 `primary_terms`，导致短查询生成 `"科幻史诗 科幻史诗"`。
- 修改后：以 `clean_pos_sentence` 为基础，**仅追加尚未作为子串出现在 `clean_pos_sentence`（及其小写形式）中的新概念词与同义词**（去重后最多追加 3 个扩展词），确保 `"科幻史诗"` 的嵌入输入纯净为 `"科幻史诗"`，不引入自我重复噪声。

#### 2. 广告目录清洗与内容字段补全（`backend/build_bge_index.py`）
- 按 `|` 切分 `toc_text`，使用正则过滤含 `公众号|免费领取|加微信|微信【|回复书名|扫一扫|关注.*获取|每日发布新书` 的广告条目后再截取拼接（解决当前 20 本已知污染书及未来入库同类电子书）。
- View B 文本模板保留现有 `PASSAGE_SCHEMA_VERSION` 思路并升级为 `v3`：在 `看点` 之后、`章节` 之前补入 `适合：{target_audience[:80]}`。

#### 3. 双视角向量设计（第三阶段，按数据决定是否实施）
- 前置条件：第一阶段修复查询去重后，短概念查询的向量召回仍明显落后（以评测集中短主题类查询的 `nDCG@10` 和召回位次衡量）。双视角会多占约 `85MB` 内存、多一套构建流程和一组融合权重，没有数据支撑不做。
- 须在工作区现有的向量热更新改动（`upsert_book_vector`、`refresh_book_vector_in_memory`）提交之后，以其为基础扩展，不另起一套接口。

- **View A — 元数据/概念短向量**（`data/bge_m3_meta_vectors.npy`，`max_length=128`）：
  仅编码高信噪比结构化标签：
  ```text
  《{clean_title or title}》 ({subtitle[:60]}) 作者：{author[:40]} 领域：{cat}/{subfield} 类型：{kind}/{level}。关键词：{'、'.join(keywords[:8])}。
  ```
  单条均值约 `50–65 tokens`，全库 21,635 本构建耗时约 `2–4` 分钟，落盘/内存可控（`float32` 约 `85MB`，可选落盘 `float16` 约 `44MB` 并在加载时转回 `float32` 以保持 BLAS `np.dot` 精度与速度）。
- **View B — 内容/剧情长向量**（`data/bge_m3_vectors.npy`，`max_length=512`）：
  编码完整信息（头信息 + 关键词 + `summary[:360]` + `ai_refreshed` 时的 `core_points[:180]` + `target_audience[:80]` + 去广告 `toc_text[:80]`）。利用 `compute_passage_hash` 进行增量更新，仅重编码本次文案有变化的书籍。
- **元数据索引文件**：`data/bge_m3_meta.json` 升级支持 `passage_version = 3`、`meta_hashes` 与 `content_hashes`，保障双矩阵 `book_ids` 行索引严格对齐。

#### 4. 双矩阵原子加载、热更新与自适应 MaxSim 融合（`backend/semantic_models.py`）
- **线程安全原子快照**：`BgeM3Engine` 使用同一份 `book_ids` 与锁维护 `(content_matrix, meta_matrix, row_arr, id_to_row)` 不可变快照元组。
- **双向量实时热更新**：扩展 `upsert_book_vectors(book_id, content_vector, meta_vector)`（保留旧 `upsert_book_vector` 兼容签名），在前端点击「AI 重新提炼」或 `enrich_ai.py` 刷新单本书后，单次调用原子替换内存中 View A 与 View B 两套矩阵行，搜索即时生效。
- **查询自适应双视角融合（视图内对齐 + MaxSim）**：
  在 BGE-M3 下，短文本（View A，约 55 tokens）对查询的余弦基线通常比长文本（View B，约 300 tokens）系统性偏高 `0.03–0.07`。若直接取原始 `max`，长查询也会普遍落到 `s_meta`，使公式退化为 View A 单边霸权。因此，在候选池（含 4.4.1 缺失行补算后）内，先对两视角原始余弦 $s_{\text{meta}}$ 与 $s_{\text{content}}$ 各自做**视图内稳健中心化 / 标准化对齐**（如减去各自池内中位数对齐到统一标尺，或各自按池内稳健分位数归一化到 $s'_{\text{meta}}, s'_{\text{content}} \in [0, 1]$），再做查询自适应 MaxSim 融合：
  $$s_{\text{dense}}(i) = 0.60 \cdot \max\bigl(s'_{\text{meta}}(i),\; s'_{\text{content}}(i)\bigr) + \beta \cdot s'_{\text{meta}}(i) + (0.40 - \beta) \cdot s'_{\text{content}}(i)$$
  其中权重 $\beta$ 按查询形态自适应：
  - **短概念 / 主题查询**（`len(q_clean) <= 6` 且 `len(primary_terms) <= 2`）：$\beta = 0.25$（偏重 View A，防止长简介稀释标签语义）；
  - **长描述 / 复杂意图查询**（`len(q_clean) > 6` 或 `len(primary_terms) >= 3`）：$\beta = 0.15$（偏重 View B，保留剧情细节、受众与看点语义）；
  - 同时保留未经中心化的**双视角最大原始余弦** $s_{\text{raw\_max}}(i) = \max(s_{\text{meta}}(i), s_{\text{content}}(i))$，专门供给 4.4.3 绝对语义救援硬门槛（`≥ 0.54`）判定使用。
- **下游接口兼容**：`handle_get_similar_books`（相似推荐）与 `search/interest.py` 继续使用 View B 内容矩阵，`get_book_matrix()` / `get_book_vectors()` 接口保持向后兼容。

---

### 4.3 数据卫生：`is_anthology` 口径统一与回修

#### 1. 问题口径对比
- 现状：`classic_scorer.py` 正则包含 `丛书` 且仅看书名/副标题，导致全库 `1,577` 本标为 `is_anthology=1` 的书中：
  - **`974` 本 `kind` 非「套装合集」（另有 `3` 本 `kind` 为空，合计 `977` 本，占 62%）为误伤**（`book_ai_meta.kind` 实为 `学术理论`/`小说文学`/`实用指南` 等，包含「汉译世界学术名著丛书」系列单行本及《银河帝国 (1-15大全集)》《三体全集》等单作者单作品全集）；
  - **`144` 本为漏标**（`is_anthology=0` 但 `book_ai_meta.kind == '套装合集'`）。

#### 2. V2 口径规则
1. 修订 `ANTHOLOGY_REGEX`：移除泛词 `丛书`。
2. 判定规则（实施时按抽样修正）：**`kind == '套装合集'` 或 书名/副标题命中去掉「丛书」后的正则**，即 `is_anthology = 1`。`kind` 描述内容体裁而非包装形式，单独作主口径会漏掉约 4 成真套装（见 Phase 1 实施记录）；名著丛书单行本视作 `is_anthology = 0`。
3. 数据库迁移回修：运行一次性回修脚本修正全库 `books.is_anthology`（预计修正约 1,121 本）并重算对应书籍的 `classic_score`（撤回被误打的 `× 0.68` 惩罚，同步更新 FTS 与兴趣推荐/评分抓取候选池）。
4. 搜索惩罚调轻：Stage-1 中仅当 `is_anthology == 1` 且 `not wants_anthology` 时施加轻量惩罚 `P_anth ∈ [-12, -8]`（取代原 `-24.0`，具体取值由 CV 校准）。

---

### 4.4 Stage-1 排序主算法：查询内归一化混合融合 + 严格分层词法证据

#### 1. 候选池查询内归一化（RRF 汇聚后，$|C| \le 180$，随 Phase 2 一同落地）
- **带分母下界保护的对数 BM25F 归一化** $\tilde{b}_i \in [0, 1]$：
  $$\tilde{b}_i = \frac{\log\bigl(1 + \max(0,\; b_i)\bigr)}{\log\bigl(1 + \max(B_{\text{floor}},\; \max_{j \in C} b_j)\bigr)}, \quad B_{\text{floor}} \text{ 仅用 80\% 调参集标定}$$
  使用对数压缩防止长简介堆砌关键词形成极端尖峰；同时引入绝对分母下界 $B_{\text{floor}}$，防止冷门查询仅有 1 本弱命中书时被分母等比例放大成满分 `1.0`。bm25 原始分的量级取决于字段权重（12/9/7…）与语料，不预设数值：全文索引建成后，**仅使用 80% 调参集**上各查询第一名 bm25 原始分的分布标定一次（如中位数 × 0.3，严禁接触 20% 留出集），之后作为结构性常量固定，不参与后续调参。
- **缺失行切片补算 + 稳健下界保护 Dense 归一化** $\tilde{d}_i \in [0, 1]$：
  当前 `dense_search(top_k=160)` 仅缓存返回前 `480` 名。在 RRF 汇聚出 `top_cand_ids`（$|C| \le 180$）后，对纯词法召回但不在 Dense Top-`480` 缓存中的候选，直接执行一次切片点积 `matrix[missing_rows] @ q_vec`（耗时 `< 0.05ms`）补全真实相似度，**严禁以 `0.0` 哨兵缺省参与统计**，再计算 $d_{\max} = \max_{j \in C} s_{\text{dense}}(j)$ 与 $d_{\text{floor}} = \max\bigl(0.36,\; P_{25}(s_{\text{dense}})\bigr)$：
  $$\tilde{d}_i = \operatorname{clip}\!\left(\frac{s_{\text{dense}}(i) - d_{\text{floor}}}{\max(0.12,\; d_{\max} - d_{\text{floor}})},\; 0,\; 1\right)$$
  （注：若 Phase 3 启用双视角并完成视图内对齐，则对 $s_{\text{dense}}$ 同理采用补全缺失后的候选池分布对齐与下界保护归一化。）消除 `#480` 边界假 `0.0` 崖跌与 $P_{25}$ 坍缩风险，分母下界 `0.12` 防止弱噪声查询被强行拉满到 `1.0`。

#### 2. 严格分层词法证据 $\tilde{\tau}_i \in [0, 1]$
对每个主概念词 $t \in \text{primary\_terms}$（附动态语料 IDF 倍率 $w_t$）：

- **Tier A · 完整词多字段饱和叠加**（不再使用互斥 `elif`）：
  按字段可信度定义基础分值：

  | 命中字段 | 基础分 $s_f$ | 说明 |
  |---|---|---|
  | 书名 / 清洁书名 / 副标题 / 指定作者精确匹配 | `1.00`（作者意图精确命中作者 `1.20`） | 最强主题锚点 |
  | `ai_keywords` / `subfield` / `tags` / `category` | `0.65` | 整词匹配（保持整词边界，防止 `'行为'` 误匹配 `'行为金融'`） |
  | `summary` | `0.62` | 简介精确包含完整概念 |
  | `core_points` / `target_audience` | `0.48` | AI 提炼看点与适用读者覆盖 |
  | `toc_text` | `0.30` | 目录章节命中 |

  单个概念词在所有命中字段上的综合证据分（主命中 + 其余辅助字段衰减叠加，设上限防刷屏）：
  $$\text{ev}_A(t) = \min\!\left(1.30,\; s_{f^*} + 0.25 \sum_{f \neq f^*} s_f\right)$$
  *示例*：《银河帝国》对 `科幻史诗` 同时命中 `ai_keywords`（`0.65`）与 `summary`（`0.62`），得 $\text{ev}_A = 0.65 + 0.25 \times 0.62 = 0.805$（强 Tier A 命中）。

- **Tier B · 复合词 2 字拆干兜底**（仅在 Tier A 未命中时触发，且要求 `len(t) ∈ {3, 4}` 且 `t not in DOMAIN_SYNONYM_MAP`）：
  提取前 2 字 $h_1 = t[:2]$、后 2 字 $h_2 = t[-2:]$（均须通过停用词、歧义短词及 Tier-2 泛词过滤）：
  - **双半词协同命中**（书籍全文同时含 $h_1$ 与 $h_2$，例如既含「科幻」又含「史诗」）：标题/元数据含至少其一给 **`0.42`**，仅简介/看点给 **`0.32`**；
  - **单边半词兜底**（只含其中一半，如仅含「科幻」或仅含「史诗」）：标题/标签给 **`0.22`**，简介给 **`0.12`**。
  - **核心数学不变量**：单边半词兜底上限 `0.22` **严格低于**任何 Tier A 完整词命中最低档（`summary = 0.62`、`core_points = 0.48`、`ai_keywords = 0.65`、`toc_text = 0.30`），从根源上消灭半词沾标题反超完整词的问题。

- **多概念覆盖与同义词融合**：
  - 按 `term_weights` 对主概念得分加权汇总；多主概念且非析取（非 `A或者B`）时，每多覆盖 1 个 Tier A 概念组追加递增协同奖励；
  - `synonym_terms`（同义词/跨语种词）与 `parent_nouns`（Tier-2 语境修饰词，沿用须与 Tier-1/同义词共现的协同门控与多义词消歧守卫）单独汇总并设上界封顶；
  - 经过饱和映射归一化为 $\tilde{\tau}_i \in [0, 1]$。

#### 3. 意图自适应配权与总分公式
定义综合词法得分 $L_i = \gamma \cdot \tilde{b}_i + (1 - \gamma) \cdot \tilde{\tau}_i$（初始 $\gamma = 0.35$），按查询意图选择主融合权重：

正交分工约定：$\tilde{b}_i$ 只负责**单概念词频/IDF 与字段权重**；$\tilde{\tau}_i$ 保留 **Tier A/B 完整词分层不变量 + 多概念覆盖协同 + `is_disjunctive` 析取取 max + Tier-2 / 多义词消歧守卫**等 BM25F 无法表达的结构性查询语义。由于单字段命中强弱同时进入 $\tilde{b}_i$ 与 $\tilde{\tau}_i$ 时存在一定共线，若 Phase 2 实测重复计分明显，则降为 $\tilde{\tau}_i$ 的**结构调节模式**：$\tilde{\tau}_i$ 不再重复叠加静态单字段分，而改为直接提供 **Tier A/B 分档封顶 + 多概念覆盖/析取/多义消歧调节系数**（使单边半词书 $L_i$ 硬封顶于完整词之下，且完整保留复合查询结构语义）。

| 意图档位 | 判定规则 | 初始配权 $(w_{\text{lex}}, w_{\text{dense}})$ |
|---|---|---|
| `EXACT_ENTITY` | 引号书名 / 精确或前缀书名意图 / 作者查书 / ISBN / 拼音 | `(0.70, 0.30)` |
| `SHORT_TOPIC` | 非实体短主题词（`len(q_clean) ≤ 8` 且主概念 ≤ 2） | `(0.55, 0.45)` |
| `NL_DESCRIBE` | 自然语言长句 / 多概念约束 / 带风格或否定条件 | `(0.42, 0.58)` |

Stage-1 最终得分：
$$S_1(i) = 100 \cdot \bigl(w_{\text{lex}} L_i + w_{\text{dense}} \tilde{d}_i\bigr) + P_{\text{cat}} + P_{\text{classic}} + P_{\text{style}} + P_{\text{anth}} + P_{\text{neg}} + P_{\text{pin}}$$

所有附加先验项严格设定有界区间（禁止先验项喧宾夺主淹没主相关性）：
- **分类先验** $P_{\text{cat}} \in [-18, +10]$（命中推断主/次分类加分，无任何主题命中且不属推断分类扣分）；
- **经典度先验** $P_{\text{classic}}$：常规查询 `[0, +10]`；`wants_classic=True` 时放大为 `[0, +28]`；
- **难度/风格匹配** $P_{\text{style}} \in [-30, +22]$（入门科普 / 硬核教材 / 技术深度约束）；
- **套装惩罚** $P_{\text{anth}} \in [-12, 0]$（仅 `is_anthology == 1` 且 `not wants_anthology`）；
- **否定词硬惩罚** $P_{\text{neg}} = -80$（命中 `negative_terms`）；
- **精确书名/作者/ISBN 置顶分** $P_{\text{pin}}$（延续 `finalize.py` 中的 `title_pin_bonus` 剔除机制，避免置顶分拉高动态截断基线而误杀其余相关书）；
- **相关性门槛与 `finalize.py` 协同重标定**：保持绝对原始余弦语义救援硬门槛在 `≥ 0.54`（启用双视角时取 $\max(s_{\text{meta}}, s_{\text{content}}) \ge 0.54$，禁止盲降至 `0.50`）。Phase 2 切换至 V2 `[0, 100]` 融合分体系时，须联动重标定 `finalize.py` 中的 `score < 18.0` 噪声底线、`min_cut = max(22.0, basis * 0.28)` 动态截断及 `match_score` 归一化分母，兼顾 `tests/test_search_units.py` 约束并维持 `Bad@5 = 0`。

#### 4. 离线 5-Fold 交叉验证权重校准（`backend/tune_search_weights.py`）
- 仅用于开发阶段离线标定，**零线上运行时依赖**；
- 在 `tests/search_eval/` 全量 `175` 条标注查询中按真实 **8 类**查询类型（`author` / `english` / `known_item` / `negation` / `nl` / `style` / `topic` / `traditional`）分层划分调参集（80%，140 条）与严格留出集（20%，35 条），调参集内跑 **5-Fold 交叉验证**；
- 仅对**已实际落地阶段的现存参数**做 3–4 维调参：$\gamma$（仅当 Phase 2 BM25F 已落地时纳入）、`SHORT_TOPIC` 与 `NL_DESCRIBE` 两档的 $w_{\text{lex}}$（$w_{\text{dense}} = 1 - w_{\text{lex}}$）、$P_{\text{anth}}$。其余（Tier B 上限、先验上限、`EXACT_ENTITY` 配权、Dense 下限与 $B_{\text{floor}}$）固定为结构性常量，不参与搜索；
- 新增专项回归用例全部进入留出集，完全不参与调参，仅在最终评估时运行一次；
- 优化目标：5-Fold 折外最大化 `nDCG@10` 并固化稳健均值；
- **统计门禁（严格 100% 样本外评价，零乐观偏差）**：将 80% 调参集上 **5-Fold 天然产出的 140 条严格折外（OOF）预测** 与 35 条留出集预测拼接，构成 **175 条 100% 样本外配对增量**（绝不混入样本内拟合结果）：
  1. **全量样本外配对自助法**：175 条 OOF+Holdout 配对 `nDCG@10` 增量的 95% 置信区间下限 **`> 0`**，且单独报告 **74 条人工校验 `gate` 子集**指标不退化（防伪标签掩盖人工标签下降）；
  2. **真实 8 类分层守卫**：考虑 `english` / `negation` / `style` / `traditional` 等小类仅 `n = 14`（标准误达 `0.05–0.08`），各类型配对置信区间不显著为负（按 8 次比较 Bonferroni 校正，用 99.4% 区间；不设均值降幅阈值）；
  3. **错误硬底线**：全量与留出集 **`Bad@5 == 0`**。留出集继续单独汇报用于观测过拟合迹象。
  4. **上线参数的样本外估计**：OOF 预测来自 5 组折内参数，并非最终上线的平均参数；另报告上线参数在 35 条留出集上的指标，作为上线版本的样本外估计。

---

### 4.5 精排层协同（`bge-reranker-v2-m3` 与 DeepSeek `AI 精排`）

1. **DeepSeek `AI 精排` 扩窗与文本增强**（`backend/ai_search.py`、`frontend/assets/js/api.js`、`backend/http_api/handlers/books.py`）：
   - 候选书文本模板补齐 `subfield`、`keywords[:6]`、`core_points[:2]`，并将 `summary` 展示上限从 `150` 适度放宽至 `200`；
   - `_cache_key()` 增加精排模板版本前缀（如 `v2|...`），自动使旧格式缓存失效；
   - 精排窗口从 **Top-20 扩至 Top-30**：后端 `recommend.py` 已允许 `ids[:30]`，前端开启 `AI 精排` 时获取前 30 本高潜力候选提交 `/api/search/ai_rerank`（页面展示仍遵循 `page_size=24`），提高对高潜力长尾书（特别是外文翻译名或概念间接匹配书）的召回天花板。
2. **本地 `bge-reranker-v2-m3` 字段补齐与实测去留门禁**（`backend/search/finalize.py`、`backend/search/recommend.py`）：
   - 传给 Cross-Encoder 的文档文本补齐 `subfield`、`keywords[:5]` 与 `core_points[:2]`，重排窗口与长度实测后定为 `Top-8, max_length=128`（`Top-12, max_length=160` 端到端 `p50 = 1940ms` 超出门禁，见 Phase 1 实施记录）；**Phase 1 保留现有 `(rr_prob - 0.15) * 75.0` 融合公式不变**，待 Phase 2 归一化落地时再同步切换为有界加权融合；
   - **数据驱动定案**：Phase 1 先补字段并把 `max_length` 放到 `160`、保留 `(rr_prob - 0.15) * 75.0` 在 175 条基准集上做 `describe` 开/关公平对比——**采用方案 A：对齐现状基线（`1364ms`），仅当 gate `nDCG@10` 配对增量的 95% 置信区间下限 `> 0` 且端到端 `p50 ≤ 1500ms` 时保留默认开启**（注：若后续需要硬性 `≤ 1200ms` 延迟预算，备选方案 B 为缩窗 `Top-8, max_length=128`，实测重排 `~926ms`）；否则将 `describe` 默认 `cross_encoder` 调为 `False`（`p50` 由 `1.36s` 降至 `≤ 200ms`）。

---

## 五、分阶段实施计划与验收门禁

每阶段完成后独立跑通门禁再进入下一阶段；Phase 2、Phase 3 是否实施由前一阶段的实测结果决定，确保问题早暴露、边界可隔离。

| 阶段 | 涉及核心文件 | 核心工作内容 | 验收门禁（须全部通过） |
|---|---|---|---|
| **Phase 0**<br>备份与专项用例 | `backups/`、`tests/search_eval/queries.json` | 1. 备份 `data/library.db` 与 `bge_m3_*`<br>2. 新增 12–15 条专项用例（含「科幻史诗」「心理史学」、英文单复数/子串、单字、繁简交叉等，只进留出集）<br>3. 保存 V1 评测快照 | 备份可校验；留出集隔离完成；V1 快照落盘 |
| **Phase 1**<br>速效修复与公平测重排（约 1 天） | `backend/search/intent.py`、`backend/build_bge_index.py`、`backend/classic_scorer.py`、`backend/search/scoring.py`、`backend/search/finalize.py`、`backend/search/recommend.py` | 1. `semantic_query` 去重<br>2. 过滤 `toc_text` 广告条目（只改编码文本，随增量更新生效；须等工作区未提交的 `build_bge_index.py` 改动提交后再做）<br>3. 合集判定（实施时按抽样结果改为 `kind == '套装合集'` 或去掉「丛书」后的正则命中，见 Phase 1 实施记录）：先抽样核对，再回填 `is_anthology`（修正 974 本 `kind` 非套装与 3 本空值误伤 + 144 本漏标）并重算 `classic_score`，评估释放书目对兴趣推荐与豆瓣抓取队列的影响<br>4. V1 打分局部修补：补齐 AI 字段与 Tier A 多字段饱和叠加（去互斥 `elif`）、Tier B 单边半词封顶在 Tier A 最低档之下、合集扣分调到 `-12`；繁体查询做查询侧简体扩展（保留原词，简体形式作同义词）并观察 `traditional` 切片收益<br>5. **按 4.5.2 补齐本地 Cross-Encoder 输入字段（实测定为 `Top-8, max_length=128` + `keywords/core_points`），保留旧融合公式，在 `p50 ≤ 1500ms` 门禁下跑 `describe` 开/关 A/B 对比定默认值** | 1. gate `nDCG@10` 不低于 V1，`Bad@5 = 0`，书架 `p50 ≤ 200ms`<br>2. `科幻史诗` 下 `2382/2366/2183` 进前 10<br>3. Cross-Encoder 默认开关有清晰 A/B 数据依据（开重排时 `p50 ≤ 1500ms`，否则 `≤ 200ms`）<br>4. 全量 `./scripts/verify.sh` 通过，`behaviour_diff` 的差异逐条可解释 |
| **Phase 2**<br>BM25F 全文索引 + 4.4 归一化融合（视 Phase 1 结果） | `backend/search/fts_index.py`（新）、`backend/database.py`、`backend/migrations.py`、`backend/search/recall.py`、`backend/search/scoring.py`、`backend/search/finalize.py` | 触发条件：Phase 1 后 gate `nDCG@10` 仍明显低于 `0.76`，或仍受 `LIKE` `LIMIT 80` 漏召回制约<br>1. 建 `books_search_fts`（`porter unicode61`、`aux_tok` 仅存 `isbn`、`tokenize_for_bm25f` 含繁简折叠与中文边界 `z0z`）+ `fts_dirty(book_id, dirty_seq)` 触发器（区分 `new.id`/`new.book_id`）+ 后台 `LIMIT 200` 分批轮询 CAS 同步（同进程可选 `event.set()` 唤醒）+ `fts_v2_ready` 就绪开关 + 离线 `rebuild_search_fts`<br>2. 核对英文单复数/子串与单字用例无退化后再移除通用主题 `LIKE` 扫表<br>3. **将 4.4.1–4.4.3 查询内归一化融合公式（含 80% 调参集标定的 $B_{\text{floor}}$、Dense 缺失行切片补算、Cross-Encoder 有界融合及 `finalize.py` 门槛协同重标定）与 BM25F 作为完整原子阶段同步落地** | 1. 离线重建完成且 `fts_dirty` 清空后 `fts_v2_ready=1`，活跃书目零漏行；分批 CAS 同步无 TOCTOU 丢失与写锁饥饿，读路径零写锁<br>2. 新旧用例（含英文单复数/子串及 `traditional`）不退化，书架 `p50 ≤ 200ms`，gate `nDCG@10` 逼近或达到 `≥ 0.76` 且 `Bad@5 = 0`<br>3. `tests/test_search_units.py` 全绿 |
| **Phase 3**<br>双视角向量（视数据） | `backend/build_bge_index.py`、`backend/semantic_models.py` | 触发条件见 4.2.3；在现有向量热更新改动提交后实施<br>1. 构建 View A 短向量与 View B v3，补齐 `upsert_book_vectors` 原子双热更<br>2. 在候选池内实现两视角稳健中心化对齐 + 自适应 MaxSim 融合 | 短主题类查询 `nDCG@10` 明显提升；双矩阵与 `bge_m3_meta.json` 校验 `missing=0, stale=0` |
| **Phase 4**<br>校准调参与 AI 精排协同 | `backend/tune_search_weights.py`（新）、`backend/ai_search.py`、`backend/http_api/handlers/books.py`、`frontend/assets/js/api.js`、`tests/search_eval/baseline.json`、`docs/adr/0005-*.md` | 1. 按 4.4.4 仅对已落地阶段现存的 3–4 个参数跑 5-Fold 校准，最终评估 175 条严格样本外（140 OOF + 35 Holdout）结果<br>2. DeepSeek AI 精排补全 AI 字段并递增版本缓存键；扩窗 `20→30` 前核算每日 DeepSeek 费用（单次 token 约增 50%，上限 ¥5/天）<br>3. 更新 `baseline.json`，新增 ADR-0005，视情况清理旧 `books_fts` 冗余数据 | 1. **统计门禁**（按 4.4.4）：175 条严格样本外（140 OOF + 35 Holdout）配对自助法 `nDCG` 增量 95% 置信区间下限 `> 0`、74 条人工 `gate` 子集不退化、真实 8 类各自 99.4% 配对区间不显著为负、`Bad@5 = 0`；留出集单独汇报观测过拟合<br>2. `verify.sh` 全绿；线上 `GET /api/books?q=科幻史诗` 端到端实测通过 |

---

## 六、风险控制、扩展边界与回滚预案

1. **防评测过拟合**：
   - 仅调已落地阶段现存的 3–4 个参数，在调参集（80%）内做 5-Fold 折外验证；困难专项回归用例全部锁进留出集（20%），绝不参与调参；
   - 受代码层结构性边界约束（例如：Tier B 单边半词上限硬编码低于 Tier A 完整词最低档；先验项严格有界），严禁针对任何特定查询或特定书名编写特判分支。
2. **双索引同步防漂移（遵循 ADR-0001）**：
   - `books_search_fts` 由触发器写入 `fts_dirty(book_id, dirty_seq)`，再由后台单线程每 2–5 秒轮询，按 `LIMIT 200` 分批执行「短读快照 → 关连接 → 分词 → `write_tx()` CAS 删除」重写（批间让出写锁防饥饿，同进程交互可选 `event.set()` 唤醒），**严禁在 `GET /api/books` 只读路径执行写同步**；切流受 `fts_v2_ready` 开关保护，未重建完毕自动回退 V1；
   - 向量热更新收敛到 `refresh_book_vector_in_memory(book_id)`，配合离线 `verify/rebuild` 校验命令与架构防漏单测，防止 `enrich_ai.py` 等多进程任务造成索引脱节。
3. **复杂度与规模扩展天花板（100× 边界）**：
   - **当前规模（$N = 21,635$）**：双 float32 矩阵常驻约 `170MB` 内存，两次精确矩阵乘复杂度 $O(N \cdot D)$ 总耗时约 `2.5ms`；BM25F FTS5 查询平均复杂度 $O(k \log N)$（`2–5ms`），仅在极高频常见 2-gram（如「中国」「心理」）交集下退化至 $O(N)$。
   - **架构适用上限（建议 $\le 20$ 万册）**：若书库规模放大 100 倍至 $N \approx 2.16 \times 10^6$ 册，当前精确矩阵乘会最先崩溃——双 float32 矩阵将膨胀至 **`~17GB` 内存**，单次暴力 `np.dot` 延迟突破 **`>250ms`**，同时高频 2-gram 位置短语交集明显变慢。一旦规模跨越 20 万册量级，须将 Dense 层替换为 ANN 近似索引（HNSW / IVF-PQ，如 `usearch` / `faiss`）。
4. **快速回滚**：
   - Phase 0 在 `backups/` 保留完整 `library.db` 备份与 `v2` 单向量副本；
   - Phase 1–4 每阶段独立提交，任一阶段若门禁未达标可直接通过 `git` 回退代码、重置 `fts_v2_ready` 并还原对应数据快照，零波及线上服务可用性。
