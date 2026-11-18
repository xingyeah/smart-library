# 0003：抽出轻量后台作业模块 `jobs.py` 并以棘轮测试防架构腐蚀

- Status: accepted

## Context

在早期迭代中，向量索引重建入口 `spawn_reindex` / `_reindex_lock` 放在 Web 传输层 [http_api/background.py](../../backend/http_api/background.py) 内，迫使 [book_enrichment_service.py](../../backend/book_enrichment_service.py)、[book_removal_service.py](../../backend/book_removal_service.py)、[auto_summary_refresher.py](../../backend/auto_summary_refresher.py)、[split_undo.py](../../backend/split_undo.py) 与 [mismerge_service.py](../../backend/mismerge_service.py) 等领域模块反向导入 `http_api`，制造顶层环与函数内延迟导入。同时单仓采用扁平 `backend/` 目录直跑模式，若无自动化约束极易再次退化。

## Decision

将进程内共享向量重建互斥锁与后台派发逻辑下沉到独立无传输层依赖的 [backend/jobs.py](../../backend/jobs.py)（由 `http_api/background.py` 与 `http_api/state.py` 仅作兼容重导出与共享引用），并在 [tests/test_batch3_decoupling.py](../../tests/test_batch3_decoupling.py) 中固化两道自动化架构棘轮（Ratchet）——禁止非 `http_api/`/`server.py` 模块导入 `http_api`、禁止 `database.py`/`migrations.py` 之外执行运行时 `CREATE TABLE`——与现有 [tests/test_code_standards_gate.py](../../tests/test_code_standards_gate.py)（500 行 / 50 行函数 / 3 层嵌套 / 3 参数上限）协同防腐蚀。

## Considered Options

- **引入 Celery / RQ + Redis 等外部任务队列中间件**：虽然物理隔离传输层与任务执行，但对于单机单进程、主要任务是本地 ONNX/Numpy 向量的场景属严重过度工程，额外引入进程守护与状态同步成本。
- **全面重构为多层包名 + 接口依赖注入容器**：改动波及全部 CLI、离线工具和历史兼容测试，ROI 远低于抽出单个共享 `jobs` 模块加静态棘轮门禁。

## Consequences

- `server.py` 与 `http_api` 对外旧别名保持零破坏；业务层与 CLI 工具可在不触碰 HTTP 服务器模块的前提下安全触发排他性 `.npy` 索引重建。
- 今后若必须在某个后台脚本里重度调度异步任务，任何逆向引入传输层或绕过 `migrations.py` 建表都会在单测阶段直接拦截红灯。
