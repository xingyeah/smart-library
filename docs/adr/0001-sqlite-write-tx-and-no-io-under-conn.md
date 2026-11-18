# 0001：单进程 SQLite WAL 写入串行化，且严禁持数据库连接跨 I/O

- Status: accepted

## Context

系统为单机本地优先架构，运行于单个 Python 进程内，但同时存在 HTTP 请求工作线程（标记状态、阅读进度、手动编辑、软删除）以及后台定时整理、多源慢速补全（Douban / Google Books / Open Library）和自动摘要润色线程（DeepSeek API）。此前多个写路径混用普通 `conn.commit()` 延迟升级写锁，甚至在持锁打开连接的区间内直接发起外部 HTTP 请求或搬运大文件，导致并发下频繁触发 `database is locked` 与读写撕裂。

## Decision

统一保留单库文件 + SQLite WAL 模式，并通过 [database.py](../../backend/database.py) 的 `write_tx()`（`isolation_level=None` + `BEGIN IMMEDIATE` + 异常自动回滚关闭）串行化写事务；同时在架构约束上强制所有读连接在提取内存快照（如 `dict(db_row)`）后立即关闭，绝不允许跨网络请求或耗时文件移动持有任何数据库连接（代表实现见 [book_enrichment_service.py](../../backend/book_enrichment_service.py)、[scheduler.py](../../backend/enrichers/scheduler.py) 与 [book_removal_service.py](../../backend/book_removal_service.py)）。

## Considered Options

- **迁移至 PostgreSQL 或独立数据库守护进程**：能原生支持多连接并发写，但违背项目“零重依赖、一键本地启动、单文件可备份回滚”的核心定位，大幅抬高个人用户的部署与维护成本。
- **应用层单一全局读写大锁**：虽能消除写冲突，但会拖慢高并发只读检索与书架分页查询，无法利用 SQLite WAL 的读写并发能力。

## Consequences

- 所有写竞争由 SQLite `BEGIN IMMEDIATE` 在锁入口处排队等待，避免读写事务升级死锁。
- 物理文件删改（如软删除移出）拆分为“短事务预标记不可见 → 文件搬运 → 短事务持久化路径，失败则回滚物理文件并复位可见性”，要求后续新增异步服务同样严格遵循“先读后关 → 外部 I/O → 短写事务提交”。
