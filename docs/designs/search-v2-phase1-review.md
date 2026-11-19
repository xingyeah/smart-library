# Search V2 Phase 1 代码审查与验证总结报告

- **审查日期**：2026-11-19
- **审查范围**：`git diff 41aa67b...HEAD`（提交 `306bb8b`：`feat(search): land search v2 phase 1 quick fixes`，涉及 12 个文件，`+649 / -99`）
- **依据规格**：[`docs/designs/search-v2-architecture.md`](search-v2-architecture.md)
- **项目规范**：[`CLAUDE.md`](../../CLAUDE.md)、[`GLOSSARY.md`](../../GLOSSARY.md)、[`docs/adr/0001`–`0004`](../adr/)、[`tests/test_code_standards_gate.py`](../../tests/test_code_standards_gate.py)

---

## 一、验证与运行环境快照

- **集成门禁（`./scripts/verify.sh`）**：全部通过
  - Python 单元与规范门禁：`Ran 336 tests in 74.488s`，`OK (skipped=5)`
  - 前端检查：`Syntax check passed (23 JS files)`，`checkJs passed (0 diagnostics)`
  - iOS 非 UI 类型检查：`swiftc` 通过（`ios/Library/ServerClient.swift:139` 含既有 Swift 6 并发隔离 warning）
- **核心效果实测**：
  - 查询「科幻史诗」：`2382`（《银河帝国》）、`2183`、`2366` 在结果中分别位居 `#1 / #3 / #4`（Phase 1 前分别为 `56 / 15 / 40`）
  - 合集标记回填：`data/library.db` 中 `is_anthology = 1` 由 `1,577` 本更新为 `1,273` 本
- **环境待处理状态**：
  - 后台服务 `PID 68095` 启动于 `2026-11-19 15:52:15`（早于 `306bb8b` 提交时间 `21:41:11`），**尚未重启加载 Phase 1 代码**
  - 工作区尚存 6 个此前未提交的向量热更新文件（`backend/book_enrichment_service.py`、`backend/build_bge_index.py`、`backend/enrich_ai.py`、`backend/semantic_models.py`、`tests/test_server_routes_characterization.py`、`tests/test_vector_enrichment.py`）以及 12 个未跟踪的 `.电子书分类索引与简介目录.*.tmp` 临时文件

---

## 二、Standards 轴审查（规范、工程质量与代码异味）

### 1. 文档规范违例（Hard Violations / 边界裁量）

| 级别 | 位置 | 问题与引用规范 |
|---|---|---|
| **Hard Violation** | `backend/search/finalize.py:67-74`<br>`tests/test_code_standards_gate.py:81` | **绕过 ADR-0003 参数上限棘轮**：`rerank_and_fuse_candidates` 原本在 `ALLOWED_PARAM_EXEMPTIONS` 中且有 5 个参数；本次新增第 6 个参数 `ai_meta=None`。因门禁只校验函数名列表未锁定参数个数，实质绕过了「公有 API ≤ 3 参数」棘轮约束，应封装配置/上下文对象（见 [ADR-0003](../adr/0003-flat-jobs-module-and-ratchet-tests.md)）。 |
| **边界裁量** | `tests/test_code_standards_gate.py:41`<br>`backend/search/intent.py:64-77` | **ADR-0003 豁免名单平移**：将约 330 行的超长豁免函数改名为 `_analyze_query_intent` 并外包一层薄壳，虽未违背「豁免条目数只减不增」的字面门禁，但属于平移旧债务而非真正拆分消减。 |

> 注：ADR-0001（单写锁与禁持连接跨 I/O）、ADR-0002（零构建 ESM + `checkJs`）、ADR-0004（iOS 离线与代理规范）及 `GLOSSARY.md` 未发现新增违例。

### 2. 错误处理、资源生命周期与边界 Bug（Judgement Calls / 高优先）

1. **繁体同义词被尾部截断且多字段漏转换**（`backend/search/hanzi.py:69-74`、`backend/search/intent.py:461`、`backend/search/recall.py:157`）：
   - `with_traditional_variants` 采用 `append` 将繁体变体追尾至 `synonym_terms` 末尾，但 `intent.py:461` 仅取 `synonym_terms[:4]`、`recall.py:157` 仅取 `synonym_terms[:6]`。对于较长或多概念繁体查询（如 `心理學與經濟學`、`投資 不要雞湯`），前部已占满本体同义词，繁体原词被静默截断丢弃（应前插或使用独立变体字段）。
   - `simplified.find(term)` 区分大小写，遇到已在前序小写化的中英混合词（`ai` vs `AI`）会失配；同时 `explicit_title` 与 `negative_terms` 丢失原始繁体形态。
2. **CoreFoundation `ctypes` 生命周期与空字节截断风险**（`backend/search/hanzi.py:14-62`）：
   - `_load_core_foundation()` 未捕获加载动态库或解析符号时的 `OSError` / `AttributeError`，一旦运行环境缺少对应库将导致模块导入直接失败，违背 docstring 降级契约；
   - 使用 `CFStringCreateWithCString` 遇到 `\x00` 会静默截断后续文本（实测 `'台灣\x00測試' -> '台湾'`），应改用带长度的 `CFStringCreateWithBytes`；
   - 第 49 行 `mutable = _CF.CFStringCreateMutableCopy(...)` 位于 `try:` 块之外，若此处抛出异常会泄漏第 47 行创建的 `source` 引用；且 `CFRelease.restype` 未显式设为 `None`。
3. **可空字段切片触发 `TypeError` 风险**（`backend/search/finalize.py:52, 55-56`）：
   - `books.subtitle` 与 `books.author` 在表定义中允许为 `NULL`，`item.get("subtitle", "")[:45]` 与 `item.get("author", "")[:25]` 当键存在且值为 `None` 时会抛出 `TypeError`（应统一 `(val or "")`）；`meta.get("keywords", [])` 亦缺少字符串元素类型过滤。
4. **`has_both_halves` 误加分**（`backend/search/scoring.py:55-56, 270`）：
   - 仅以纯 `in` 子串匹配且未过滤停用词、`TIER2_CONTEXT_MODIFIERS` 及非 CJK 词，导致 3–4 字母英文词（如 `rust` / `ruby`）或含泛词后缀的中文词误拿双半词奖励 `BOTH_HALVES_*_PTS`。

### 3. Baseline Smells（Judgement Calls / 参考重构建议）

- **Duplicated Code**（`backend/search/scoring.py:43` vs `238-260`）：`STACKABLE_FIELD_PTS` 定义的 4 档分值 `(44/36/30/22)` 在下文 `elif` 链内各重复一遍，后续 Phase 4 调参易漂移。
- **Data Clumps**（`backend/search/retrieval.py:116-135`、`backend/search/finalize.py:67-74`）：`raw_query` / `intent` / `ai_meta` 跨阶段以散装参数传递，直接推高函数参数个数并触发 ADR-0003 旁路。
- **Primitive Obsession**（`backend/classic_scorer.py:171`）：`is_anthology_book` 使用布尔前缀命名却返回 `0 / 1` 整型（为兼容库内 SQLite 列类型所致，低优先）。

---

## 三、Spec 轴审查（规格符合度与范围控制）

### 1. (a) 缺失或部分实现项

| 项目 | Spec 引用位置 | 核实情况 |
|---|---|---|
| **Phase 0 专项用例与 V1 快照缺失** | `L371`（Phase 0 栏） | `tests/search_eval/queries.json` 仍为 34 条，未见新增 12–15 条专项留出用例，也未见 V1 评测对比快照；`backups/search-v2-phase0/` 下仅有向量备份，数据库备份仅在 `backups/library-20261119-175655.db`。 |
| **`toc_text` 广告过滤延后** | `L229-230`、`L372`（#2） | 因 `backend/build_bge_index.py` 存在前置未提交修改而暂缓实施；库内仍有 7 本带公众号引流广告目录的书未清洗。 |
| **`scoring.py` 未补齐 4 个 AI 字段** | `L293-299`、`L372`（#4） | `backend/search/shelf.py` 已补加载 `subfield`，但 `scoring.py` 仍未参与匹配 `clean_title`、`subfield`、`core_points` 与 `target_audience`。 |
| **繁→简缺少手写兜底映射表** | `L74`（F2）、`L162` | `backend/search/hanzi.py:44-45` 在 CoreFoundation 不可用时直接返回原文，缺少跨平台/容器降级所需的轻量繁→简映射兜底表。 |
| **缺少合集释放的下游影响评估** | `L372`（#3） | 已回填 `is_anthology` 并取消 448 本原标记，但未记录该批书目重新进入 `interest.py` 推荐池与 `scheduler.py` 外部评分抓取队列后的数量与排序影响。 |

### 2. (b) 范围蔓延（Scope Creep）

- **在现有评测集与 `gate` 子集直接增补未复核标签**（`tests/search_eval/generated_queries.json`，对照 Spec `L83`、`L346`、`L371`）：
  - Spec 仅规划新增独立留出回归用例；本次使用 DeepSeek `label_one` 在 `generated_queries.json` 净增 `219` 条未经人工逐条复核的标签（`L83` 记为补标 248 本），其中 **`98` 条直接写入 5 条标记了 `verified=true` 的 `gate` 门禁查询**，削弱了 `gate` 作为「纯人工校验基准」的独立性。

### 3. (c) 实现偏离与细微缺陷

1. **繁体扩展顺序反转引发 4 处功能偏离**（Spec `L74`、`L162`、`L372` vs `hanzi.py:65-75`、`intent.py:64-74`）：
   - Spec 约定「保留原词，简体作为同义词扩展」；实际实现改为「入口先转简体，再将繁体原词 `append` 到 `synonym_terms` 尾部」，引发：
     1. **`explicit_title` 被简体化**：繁体搜书名（如 `刻意進化`）变为 `刻意进化`，错失繁体原书名 SQL 精确匹配与 `+480` 置顶加分；
     2. **原词遭 `[:6]` / `[:4]` 截断丢弃**：多概念繁体查询（如 `機器學習與深度學習`、`時間管理與專注力`）的繁体原词被本体同义词挤出前 6 位；
     3. **大小写敏感漏拼词**：`simplified.find(term)` 区分大小写，中英混排小写化的 `primary_terms` 找不到繁体对应段；
     4. **否定词/上位词未同步扩充**：如 `不要小說` 中的繁体排除词未同步映射到简体/繁体两套文档字段。
2. **`build_semantic_query` 缺少 3 词扩展上限**（Spec `L227` vs `backend/search/intent.py:432-440`）：Spec 规定去重后最多追加 3 个扩展词，代码未截断（长/多概念查询最多可追加十余词，仍可能拉低短查询向量的聚焦度）。
3. **Tier B 双半词未滤泛词且短路漏判标题命中**（Spec `L305-309` vs `scoring.py:55-56, 271-294`）：
   - `has_both_halves` 未过滤 `CONVERSATIONAL_STOPWORDS` / `AMBIGUOUS_SHORT_DOMAIN_KEYS` / `TIER2_CONTEXT_MODIFIERS`（例如 `投资方法` 仍触发 `both_halves=True` 得 `30.0`，压过完整词 `toc=22.0`）；
   - 遍历半词命中字段时，若先检查到的 `h1` 仅命中 `summary` 即执行 `break`，会跳过 `h2` 可能命中 `title_sub` 的分支，漏掉应得的 `BOTH_HALVES_TITLE_PTS`。
4. **Cross-Encoder 默认开启未补全 `gate` 门禁实测依据**（Spec `L361` 4.5.2 vs `L84-85`）：Spec 要求开启条件为「`gate nDCG@10` 95% CI 下限 `> 0` 且 `p50 ≤ 1500ms`」；`L84-85` 记录 `Top-12/160` 超时（`1940ms`）且 `gate` CI 含 `0`，切到备选 `Top-8/128`（`992ms`）后仅汇报了全量 CI（`+0.012..+0.046`），未记录切配置后的 `gate` 子集 CI 即保留默认开启。
5. **Spec 文档正文口径未同步更新**（Spec `L97`、`L269-270`、`L372` vs `L81-85`）：Phase 1 实施记录（`L81-85`）已依据实测修改了合集公式（改回「`kind == 套装合集` OR 去丛书正则」）、Cross-Encoder 参数（`Top-8 / 128`）与繁体处理顺序，但文档正文 Q5/4.3/4.5.2/五 仍保留旧表述，存在前后自相矛盾。

---

## 四、双轴总结与后续清理清单

- **双轴总结**：
  - **Standards**：共 9 项（1 项硬违例、1 项边界裁量、4 项高优生命周期/边界 Bug、3 项重构异味）；轴内最重问题为 `rerank_and_fuse_candidates` 新增第 6 参数旁路 ADR-0003 参数上限棘轮，以及 `hanzi.py` 在 `ctypes` 异常清理/截断与繁体同义词追尾截断上的隐患。
  - **Spec**：共 11 项（5 项缺失/部分实现、1 项范围蔓延、5 项实现偏离）；轴内最重问题为向 5 条 `verified=true` 的人工 `gate` 查询写入 98 条未复核 DeepSeek 标签（污染门禁基线），以及繁体扩词顺序反转导致精确书名失置顶、变体截断。

- **建议处理顺序（进入 Phase 2 前收口）**：
  1. **P0（数据与测试边界隔离）**：将 `tests/search_eval/generated_queries.json` 中写入 `verified=true` 查询的 98 条新标注剥离或经人工抽检核准；补齐 `tests/search_eval/queries.json` 的独立留出回归用例与 V1 快照基线。
  2. **P0（Phase 1 高优缺陷修补）**：
     - 修正 `hanzi.py`（改用 `CFStringCreateWithBytes`、将 `CFStringCreateMutableCopy` 纳入 `try` 并捕获库加载异常、显式前插或独立返回繁体变体而非追尾 `synonym_terms`、大小写不敏感提取）；`intent.py` 为 `explicit_title` / `negative_terms` 保留繁体原词，并给 `build_semantic_query` 加上 3 词扩展上限；
     - 修正 `finalize.py` 第 52/55/56 行空值切片防御，并将 `rerank_and_fuse_candidates` 散装参数收口为配置/上下文对象以消除 ADR-0003 旁路；
     - 修复 `scoring.py` 中 `has_both_halves` 的泛词/非 CJK 过滤与标题短路问题（避免 `h1` 中简介 `break` 丢 `h2` 标题高分）。
  3. **P1（补齐遗漏项与运维就绪）**：先收口工作区 6 个向量热更新未提交改动，随后补齐 `toc_text` 广告过滤、`scoring.py` 的 4 个 AI 字段匹配、`hanzi.py` 降级简表与 Cross-Encoder `Top-8/128` 在 `gate` 子集上的 CI 校验；同步更新 `docs/designs/search-v2-architecture.md` 正文消除自相矛盾；清理根目录临时 `.tmp` 文件并重启后台服务（替代旧 PID `68095`）。
