# 智能图书馆检索算法 V2 架构设计方案

- **日期**：2026-11-19
- **状态**：已评审修订（2026-11-19，见「〇、评审修订摘要」）/ 未实施
- **覆盖范围**：书架检索（`GET /api/books?q=...`）、按描述找书（`POST /api/recommend/describe`）、本地 Cross-Encoder（`bge-reranker-v2-m3`）、DeepSeek AI 精排（`POST /api/search/ai_rerank`）及对应词法/向量/数据底座

---

## 〇、评审修订摘要（2026-11-19）

评审时按代码和数据逐条核实了诊断。结论：方向可行，但原方案把几处小改动就能解决的问题，和全文索引重建、双向量、打分重写绑在一起，且可调参数过多。本次修订如下，正文相应章节已同步改写。

| 项目 | 核实结果 | 修订 |
|---|---|---|
| 「科幻史诗」下 2382 排第 56 | 实测属实（58 条中第 56；2366 第 40，2183 第 15） | 保留 |
| `semantic_query` 重复拼词 | 实测为 `'科幻史诗 科幻史诗'` | 保留，移入第一阶段 |
| `is_anthology` 误标 | 1,577 本中 974 本 `kind` 非「套装合集」；144 本漏标 | 保留，移入第一阶段；回填前抽样核对 `kind` |
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

---

## 一、决策对齐记录（Q1–Q8）

| 编号 | 决策主题 | 已确认方案 | 核心理由与取舍 |
|---|---|---|---|
| **Q1** | 升级定位与延迟预算 | **分阶段本地检索改进**（书架 `p50` 不劣于现状，即 `≤ 200ms`；描述找书 `≤ 1.2s`） | 把无 AI 时的本地检索质量拉近 LLM 水平，同时为 AI 精排输送更准候选池 |
| **Q2** | 词法召回层（Sparse） | **字段级位置 Bigram + 词元 FTS5（单表 BM25F）** | 零新依赖，解决 ≥3 字中文在 `unicode61` 下 0 命中的问题，索引 `book_ai_meta` 与 `target_audience`，取代慢 `LIKE` 扫表 |
| **Q3** | 向量表示层（Dense） | **双视角多向量索引**（元数据短向量 View A + 内容长向量 View B，自适应 MaxSim）+ 修复 `semantic_query` 重复拼词与广告目录过滤 | 兼顾短概念不被长简介稀释与长句剧情匹配，查询仅增 `~2.5ms`，短向量全库构建仅需数分钟 |
| **Q4** | 粗排融合与打分范式 | **查询内归一化混合融合 + 严格分层词法证据**，并在 175 条基准集上做 5-Fold 交叉验证校准 | 消除跨查询分数尺度漂移与魔数耦合，保证可解释性与稳定泛化 |
| **Q5** | 合集惩罚与数据卫生 | **以 `book_ai_meta.kind == '套装合集'` 为主口径修正 `is_anthology` 并回修 DB**；轻度降权（`-8 ~ -12`） | 解除对 977 本单部名著/单系列全集（占原标记 62%）的误伤与 `-24` 重罚，释放回推荐与评分队列 |
| **Q6** | 复合词拆干策略 | **严格分层 + 双半词协同**：完整词多字段饱和叠加居高优；2 字拆干仅作低权兜底，双半词协同明显高于单边半词 | 保证「单边沾半词」在数学上永不反超「完整词命中」 |
| **Q7** | 精排层协同 | **DeepSeek 精排与本地 Cross-Encoder 同步补齐 AI 字段 + DeepSeek 扩窗 20→30**；本地 `bge-reranker-v2-m3` 按实测门禁决定默认去留 | 本地重排当前耗时 `~1.2s` 且拉低 gate `nDCG`，需在补齐字段与校准融合后用实测数据决定去留 |
| **Q8** | 验收门禁与执行方式 | **先落盘设计文档审定**；实施时按 Phase 0–4 分阶段推进并执行严格备份与量化门禁 | 降低大规模索引重建与排序重写风险，每阶段独立可验证、可回滚 |

---

## 二、问题诊断与现状基线

### 2.1 量化基线（实测 175 条基准集）

| 评估维度 | 当前现状（V1） | V2 目标 |
|---|---|---|
| 书架本地检索 (`shelf`) | 全量 `nDCG@10 = 0.6721`（gate 74 条 `0.7035`），`P@5 = 0.7269`，`Hit@3 = 0.8114`，`Bad@5 = 0`，`p50 = 190ms`（全量 `210ms`） | gate `nDCG@10 ≥ 0.76`，`Hit@3 ≥ 0.88`，`Bad@5 = 0`，`p50 ≤ 200ms`（73c3a7d 合并 `LIKE` 扫描后实测 `184ms`） |
| 按描述找书 (`describe`) | 全量 `nDCG@10 = 0.6861`（gate 74 条 `0.7015`），`Hit@3 = 0.84`，`Bad@5 = 0`，`p50 = 1364ms`（全量 `1369ms`） | gate `nDCG@10` 显著优于 V1；`p50 ≤ 1200ms`（若本地重排无正收益则降为 `≤ 250ms`） |
| AI 精排上限 (`baseline_ai`) | `shelf` 全量 `nDCG@10 = 0.8636`（gate 54 条 `0.8387`，`Hit@3 = 1.0`）；仅排粗排前 20 且缺 `keywords/core_points` | 提高粗排 Top-30 召回密度并补全 AI 字段，缩小离线检索与在线 LLM 之间约 `0.19` 的 `nDCG` 鸿沟 |

### 2.2 典型案例根因解剖（查询「科幻史诗」）

检索 `科幻史诗` 时，已有新提炼简介与 AI 关键词「科幻史诗」的《银河帝国 (Foundation)》（`id=2382`）在 `GET /api/books` 仅排第 **56 / 58 名**（`77.80` 分 vs 第 1 名 `122.61` 分），由以下 4 项结构性缺陷叠加导致：

1. **词法权重倒挂**：`scoring.py` 中 `summary`（`36.0 × w_pt = 63.68`）与 `ai_keywords` 处于互斥 `elif` 链无法叠加；而全文不含「科幻史诗」的书落入 2 字拆干分支，标题沾半个词 `科幻` 或 `史诗` 即可拿 `52.0 × 0.78 × w_pt = 71.75` 分，导致 55 本仅含半词的书集体压过全库仅有的 3 本完整命中书（`id=2183/2366/2382`）。
2. **`is_anthology` 误伤**：`classic_scorer.py` 正则包含 `丛书`，导致全库 `1,577` 本 `is_anthology=1` 中有 **`977` 本（62%）** 被误标（`book_ai_meta.kind` 实为学术理论/小说文学等，包含《物种起源》《理想国》《君主论》《银河帝国》），搜索直扣 `-24.0` 分且 `classic_score` 被打 `0.68` 折；另有 `144` 本真套装漏标。
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
 │     │            + 精确书名 / 作者 / ISBN / 拼音索引快速通道（取缔慢 LIKE 全表扫描）
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
- **分段与防跨句哨兵**：按标点、空白和字段边界切分，相邻段之间插入字母数字哨兵词元 `z0z`，防止上一段尾字与下一段首字拼接出虚假 2-gram（如关键词列表、章节边界、不同字段拼接处）。
- **中文连续串（长度 $L \ge 2$）**：输出相邻重叠 2-gram（`s[i:i+2]`），例如 `科幻史诗` → `科幻 幻史 史诗`（相邻位置 $p, p+1, p+2$）。长度为 1 的孤立汉字保留原字。
- **英文与数字串**：转小写作为完整词元保留；特殊技术标识符（如 `c++` → `cpp`、`c#` → `csharp`、`f#` → `fsharp`、`node.js` → `nodejs`）在索引与查询两侧同构映射，保证 `ai`、`go`、`llm`、`python` 等词精确匹配。

#### 2. 查询表达式转换 `term_to_fts_expr(term: str) -> str`
- 2 字中文 `科幻` → `"科幻"`；
- ≥3 字中文 `科幻史诗` → FTS5 位置短语 `"科幻 幻史 史诗"`（FTS5 严格要求 `科幻` 紧邻 `幻史` 紧邻 `史诗`，数学上等价于连续子串精确匹配，兼具 C 级倒排速度与字段 BM25 得分）；
- 英文词 `foundation` → `"foundation"`；
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
    aux_tok,     -- isbn + 规范化全拼 / 首字母                           (BM25 weight  3.0)
    tokenize='unicode61'
);
```

- 说明：`books_search_fts` 采用独立内容存储，确保可在单个 FTS 表内聚合 `books` 与 `book_ai_meta` 两张表的字段（`clean_title`、`keywords`、`ai_category`、`subfield`）。
- 检索取回稀疏分：`raw_bm25 = -bm25(books_search_fts, 12.0, 9.0, 7.0, 4.0, 3.5, 2.0, 3.0)`（因 SQLite FTS5 `bm25()` 返回负值，取反后越大表示相关度越高），作为连续信号直接传入 Stage 4。

#### 4. 一致性同步与旧表瘦身
- 写入点不手动调用同步：在 `books`（字段白名单 `WHEN` 守卫，同现有 `books_fts` 触发器）与 `book_ai_meta` 上建纯 SQL 触发器，把变动书的 `book_id` 写入待同步表 `fts_dirty(book_id PRIMARY KEY)`。以后新增写入点也不会漏。
- `backend/search/fts_index.py` 的 `sync_dirty_fts(conn)` 读取 `fts_dirty`，用 Python 分词器重写这些书的行后清空对应记录；在检索前（待同步行数很少时）及后台定时任务中调用。
- 全量重建 `rebuild_search_fts(conn)` 是离线命令，不放进服务启动时执行的迁移（Python 分词 2 万本会拖慢启动）；迁移只建空表和触发器，并把全部活跃书写入 `fts_dirty`。
- 软删除（`is_deleted=1`）与物理删除同样经触发器进入 `fts_dirty`，同步时删除对应行，避免幽灵行残留。
- 待 Phase 1 全量验证通过后，可在后续清理阶段剥离旧 `books_fts` 中高达 `166MB` 的冗余 `pinyin_search` 索引数据，整体数据库预计净减体积 `60–90MB`。

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
- **查询自适应双视角融合**：
  在 `dense_search` 中分别计算 $s_{\text{meta}} = M_{\text{meta}} q$ 与 $s_{\text{content}} = M_{\text{content}} q$（两次 BLAS `np.dot` 总耗时 `~2.5ms`），融合相似度定义为：
  $$s_{\text{dense}}(i) = 0.60 \cdot \max\bigl(s_{\text{meta}}(i),\; s_{\text{content}}(i)\bigr) + \beta \cdot s_{\text{meta}}(i) + (0.40 - \beta) \cdot s_{\text{content}}(i)$$
  其中权重 $\beta$ 按查询形态自适应：
  - **短概念 / 主题查询**（`len(q_clean) <= 6` 且 `len(primary_terms) <= 2`）：$\beta = 0.25$（偏重 View A，防止长简介稀释标签语义）；
  - **长描述 / 复杂意图查询**（`len(q_clean) > 6` 或 `len(primary_terms) >= 3`）：$\beta = 0.15$（偏重 View B，保留剧情细节、受众与看点语义）。
- **下游接口兼容**：`handle_get_similar_books`（相似推荐）与 `search/interest.py` 继续使用 View B 内容矩阵，`get_book_matrix()` / `get_book_vectors()` 接口保持向后兼容。

---

### 4.3 数据卫生：`is_anthology` 口径统一与回修

#### 1. 问题口径对比
- 现状：`classic_scorer.py` 正则包含 `丛书` 且仅看书名/副标题，导致全库 `1,577` 本标为 `is_anthology=1` 的书中：
  - **`977` 本（62%）为误伤**（`book_ai_meta.kind` 为 `学术理论`/`小说文学`/`实用指南` 等，包含「汉译世界学术名著丛书」系列单行本及《银河帝国 (1-15大全集)》《三体全集》等单作者单作品全集）；
  - **`144` 本为漏标**（`is_anthology=0` 但 `book_ai_meta.kind == '套装合集'`）。

#### 2. V2 口径规则
1. 修订 `ANTHOLOGY_REGEX`：移除泛词 `丛书`，仅用于**无 `book_ai_meta.kind`** 的极少数未提炼新书（当前仅 ~82 本）回退兜底。
2. 权威判定规则：存在 `book_ai_meta.kind` 时，严格以 **`kind == '套装合集'`** 作为 `is_anthology = 1` 主口径；单作者系列全集与名著丛书单行本均视作 `is_anthology = 0`。
3. 数据库迁移回修：运行一次性回修脚本修正全库 `books.is_anthology`（预计修正约 1,121 本）并重算对应书籍的 `classic_score`（撤回被误打的 `× 0.68` 惩罚，同步更新 FTS 与兴趣推荐/评分抓取候选池）。
4. 搜索惩罚调轻：Stage-1 中仅当 `is_anthology == 1` 且 `not wants_anthology` 时施加轻量惩罚 `P_anth ∈ [-12, -8]`（取代原 `-24.0`，具体取值由 CV 校准）。

---

### 4.4 Stage-1 排序主算法：查询内归一化混合融合 + 严格分层词法证据

#### 1. 候选池查询内归一化（RRF 汇聚后，$|C| \le 180$）
- **对数压缩 BM25F 归一化** $\tilde{b}_i \in [0, 1]$：
  $$\tilde{b}_i = \frac{\log(1 + \max(0,\; b_i))}{\log(1 + \max_{j \in C} b_j + 10^{-6})}$$
  使用对数压缩防止个别长简介堆砌关键词的书在 BM25 上形成极端尖峰而压扁其他相关书的分差。
- **稳健下界保护 Dense 归一化** $\tilde{d}_i \in [0, 1]$：
  设 $d_{\max} = \max_{j \in C} s_{\text{dense}}(j)$，$d_{\text{floor}} = \max\bigl(0.36,\; P_{25}(s_{\text{dense}})\bigr)$：
  $$\tilde{d}_i = \operatorname{clip}\!\left(\frac{s_{\text{dense}}(i) - d_{\text{floor}}}{\max(0.12,\; d_{\max} - d_{\text{floor}})},\; 0,\; 1\right)$$
  分母硬下界 `0.12` 保证噪声查询不会将全池 `0.38` 左右的弱相似分强行拉满到 `1.0`。

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
- **相关性门槛**：沿用并基于归一化信号校准「无主题命中且 `s_dense < 0.50` 且 `_bm25_norm < 0.30`」的噪声过滤门限，配合 `_finalize_ranked_pool` 去重与尾部截断。

#### 4. 离线 5-Fold 交叉验证权重校准（`backend/tune_search_weights.py`）
- 仅用于开发阶段离线标定，**零线上运行时依赖**；
- 在 `tests/search_eval/` 全量 `175` 条标注查询上按查询类型（`author` / `known_item` / `nl` / `generated`）分层划分 **5-Fold**；
- 只调 3–4 个参数：$\gamma$、`SHORT_TOPIC` 与 `NL_DESCRIBE` 两档的 $w_{\text{lex}}$（$w_{\text{dense}} = 1 - w_{\text{lex}}$）、$P_{\text{anth}}$。其余（Tier B 上限、先验上限、`EXACT_ENTITY` 配权、Dense 下限）固定为结构性常量，不参与搜索；
- 调参前随机留出约 20% 查询作为留出集，完全不参与调参，只在最后报告一次；新增的专项回归用例也只进留出集，避免照着已知失败调参；
- 优化目标：最大化折外（Out-of-Fold）平均 `nDCG@10`，同时满足硬约束 `Bad@5 == 0` 且折内每条已知回归用例无明显退化，将 5 折稳健均值常量固化至 `backend/search/scoring.py`。

---

### 4.5 精排层协同（`bge-reranker-v2-m3` 与 DeepSeek `AI 精排`）

1. **DeepSeek `AI 精排` 扩窗与文本增强**（`backend/ai_search.py`、`frontend/assets/js/api.js`、`backend/http_api/handlers/books.py`）：
   - 候选书文本模板补齐 `subfield`、`keywords[:6]`、`core_points[:2]`，并将 `summary` 展示上限从 `150` 适度放宽至 `200`；
   - `_cache_key()` 增加精排模板版本前缀（如 `v2|...`），自动使旧格式缓存失效；
   - 精排窗口从 **Top-20 扩至 Top-30**：后端 `recommend.py` 已允许 `ids[:30]`，前端开启 `AI 精排` 时获取前 30 本高潜力候选提交 `/api/search/ai_rerank`（页面展示仍遵循 `page_size=24`），提高对高潜力长尾书（特别是外文翻译名或概念间接匹配书）的召回天花板。
2. **本地 `bge-reranker-v2-m3` 字段补齐与实测去留门禁**（`backend/search/finalize.py`、`backend/search/recommend.py`）：
   - 传给 Cross-Encoder 的文档文本补齐 `subfield`、`keywords[:5]` 与 `core_points[:2]`，并将 `max_length` 从 `104` 放宽至 `160`（实测 Top-12 单批耗时 `~1.23s`），分数融合从原 `(rr_prob - 0.15) * 75.0` 线性叠加改为与归一化 `S_1` 的有界加权融合；
   - **数据驱动定案**：Phase 4 在 175 条基准集上做 A/B 实测对比——**仅当 `describe` 模式下启用本地 Cross-Encoder 能带来 gate `nDCG@10 ≥ +0.01` 的净增益且 `p50 ≤ 1200ms` 时保留默认开启**；若仍为负收益或无显著增益，则将 `describe` 默认 `cross_encoder` 调为 `False`（延迟由 `1.36s` 降至 `~200ms`）。

---

## 五、分阶段实施计划与验收门禁

每阶段完成后独立跑通门禁再进入下一阶段；第二、三阶段是否实施由前一阶段的实测结果决定。

| 阶段 | 涉及核心文件 | 核心工作内容 | 验收门禁（须全部通过） |
|---|---|---|---|
| **Phase 0**<br>备份与专项用例 | `backups/`、`tests/search_eval/queries.json` | 1. 备份 `data/library.db` 与 `bge_m3_*`<br>2. 新增 12–15 条专项用例（只进留出集）<br>3. 保存 V1 评测快照 | 备份可校验；快照落盘 |
| **Phase 1**<br>速效修复（低风险，约一天） | `backend/search/intent.py`、`backend/build_bge_index.py`、`backend/classic_scorer.py`、`backend/search/scoring.py`、`backend/search/recommend.py` | 1. `semantic_query` 去重<br>2. 过滤 `toc_text` 广告条目（只改编码文本，随增量更新生效）<br>3. 合集判定以 `kind == '套装合集'` 为准：先抽样核对 50 本，再回填 `is_anthology` 并重算 `classic_score`；评估约 974 本回到兴趣推荐与豆瓣抓取队列的影响<br>4. 打分：Tier A 多字段可叠加（去掉互斥 `elif`），单边半词得分封顶在 Tier A 最低档之下；合集扣分调到 `-12`<br>5. `describe` 本地 Cross-Encoder 开/关对比，定默认值 | 1. gate `nDCG@10` 不低于 V1，`Bad@5 = 0`，书架 `p50 ≤ 200ms`<br>2. `科幻史诗` 下 2382/2366/2183 进前 10<br>3. 全量 `./scripts/verify.sh` 通过，`behaviour_diff` 的差异逐条可解释 |
| **Phase 2**<br>BM25F 全文索引（视 Phase 1 结果） | `backend/search/fts_index.py`（新）、`backend/migrations.py`、`backend/search/recall.py` | 触发条件：Phase 1 后 gate `nDCG@10` 仍明显低于 `0.76`<br>1. `books_search_fts` + `fts_dirty` 触发器同步 + 离线全量重建命令<br>2. 补英文子串、单字用例后再决定是否移除 `LIKE` | 1. 全文索引与活跃书目对齐，`fts_dirty` 清空后零漏行<br>2. 新用例不退化，`p50 ≤ 200ms` |
| **Phase 3**<br>双视角向量（视数据） | `backend/build_bge_index.py`、`backend/semantic_models.py` | 触发条件见 4.2.3；在现有向量热更新改动提交后实施 | 短主题类查询 `nDCG@10` 明显提升；双矩阵 `missing=0, stale=0` |
| **Phase 4**<br>调参与精排协同 | `backend/tune_search_weights.py`（新）、`backend/ai_search.py`、`frontend/assets/js/api.js` | 1. 按 4.4.4 只调 3–4 个参数<br>2. AI 精排补字段；窗口扩到 30 前估算每日 DeepSeek 费用（单次 token 约增 50%，上限 ¥5/天）<br>3. 更新 `baseline.json`，新增 ADR-0005 | 留出集指标不低于调参集；`verify.sh` 全绿；线上 `GET /api/books?q=科幻史诗` 端到端通过 |

---

## 六、风险控制与回滚预案

1. **防评测过拟合**：
   - 所有标量权重必须通过 5-Fold 折外验证（不看全量集调参），并受代码层结构性边界约束（例如：Tier B 单边半词上限硬编码低于 Tier A 完整词最低档；先验项严格有界）；
   - 严禁针对任何特定查询或特定书名编写特判分支。
2. **双索引同步防漂移**：
   - SQLite `books_search_fts` 的变动由触发器写入 `fts_dirty`，再由 `sync_dirty_fts(conn)` 统一重写，写入点无需手动调用；向量热更新沿用 `refresh_book_vector_in_memory(book_id)`；
   - 增加架构防漏单测与离线 `verify/rebuild` 一致性检查命令，防止 `enrich_ai.py` 等多进程任务造成索引脱节。
3. **快速回滚**：
   - Phase 0 在 `backups/` 保留完整 `library.db` 备份与 `v2` 单向量副本；
   - Phase 1–4 每阶段独立提交，任一阶段若门禁未达标可直接通过 `git` 回退代码并还原对应数据快照，零波及线上服务可用性。
