# 0002：维持零构建原生 ESM 前端，并用 jsconfig + checkJs 补齐静态门禁

- Status: accepted

## Context

前端共有 14 个原生 ES Module（[frontend/assets/js/](../../frontend/assets/js/)）直接由后端静态服务交付，无需打包即可在桌面浏览器与 iOS `WKWebView` 内核中按需热更新，但也曾因此长期缺少静态类型约束——此前一次重命名与模块拆分漏掉了 `pub_period` / `MOBILE_QUERY` / `MOBILE_FONTS` / `scrollToReaderAnchor` 挂载等运行时错误，直到实测审视才被发现。

## Decision

继续保持前端“零编译、零产物目录、原生 ESM 直接交付”的运行时架构，不引入 bundler 或 `.ts` 编译产物；同时在仓储侧落地 [frontend/jsconfig.json](../../frontend/jsconfig.json)、[frontend/types/globals.d.ts](../../frontend/types/globals.d.ts) 与跨平台校验脚本 [scripts/check_frontend_js.mjs](../../scripts/check_frontend_js.mjs)，同时执行 `node --check` 语法检查与 TypeScript `checkJs` 零构建静态诊断，并接入单元测试 [tests/test_batch3_decoupling.py](../../tests/test_batch3_decoupling.py) 作为回归门禁。

## Considered Options

- **引入 Vite + npm 构建链及完整 TypeScript（`.ts`）源码**：类型系统最完备，但要求用户本机维护 `node_modules` 构建流水线、在 `dist/` 与 iOS 离线壳静态缓存同步之间维护映射，破坏个人本地仓库的零构建简洁性。
- **仅靠人工评审与简单正则检查（如 `test_frontend_imports.py`）**：无法捕获 DOM 属性误用、返回值类型漂移、未定义全局调用（如此前遗留的 `doSearch()` 死分支）及跨模块契约不一致。

## Consequences

- 浏览器与 iOS App 加载的仍是原始 `.js` 源码，Web 修复零构建延迟直接触达客户端；`types/` 放在 `frontend/types/`（不放在 `/assets/` 下），不影响静态资产路由边界。
- 通过最小化 JSDoc `@type` / `@returns` 注解即可在非严格 `checkJs` 下维持 **0 诊断** 基线；未装全局 `tsc` 时脚本自动兼容复用本地 `typescript` 或本机 IDE 附带的 `typescript.js`（可用 `--require-ts` 强制校验）。
