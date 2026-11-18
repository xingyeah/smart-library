# 0004：iOS 采用自定义 Scheme 透明缓存代理与串行化磁盘读写队列

- Status: accepted

## Context

iOS 端需要复用 Web 端不断演进的阅读器（含 EPUB/MOBI/PDF 图文重排与双语翻译），并在局域网（HTTP IP）与 Tailscale 外网间切换、支持离线整本阅读与阅读进度回放。若由前端 JS 直接持有口令或使用浏览器原生机制，会受限于局域网非安全上下文（Service Worker 不可用）及跨域问题；同时 `WKURLSchemeHandler` 跑在 `@MainActor` 下若直接执行同步文件 I/O、丢弃服务端 `ETag`/`Cache-Control` 或将页面取消请求当成断网回落离线缓存，会导致卡顿、缓存失效与内容错乱。

## Decision

沿用自定义 `library://app/` Scheme 由 [LibrarySchemeHandler.swift](../../ios/Library/LibrarySchemeHandler.swift) 与 [ServerClient.swift](../../ios/Library/ServerClient.swift) 做原生层统一鉴权代理，并在稳态修复中固化三项原则：
1. **缓存协议保留**：透传 `If-None-Match` 并回传 `ETag` 与 `Cache-Control`；仅对壳页 HTML 与 `/assets/*`（或响应缺失缓存头时）强制 `no-cache`，确保前端更新即时生效同时让封面、vendor 库与 PDF 渲染页享受本地浏览器缓存。
2. **精确生命周期隔离**：追踪每个 `WKURLSchemeTask` 对应的 Swift `Task`，收到 `webView(_:stop:)` 时立即取消底层请求且直接终止，不误判为断网去读 `OfflineResponder`；队列进度清理按请求体比对防 TOCTOU 竞态覆盖新进度。
3. **离线存储串行化**：[OfflineStore.swift](../../ios/Library/OfflineStore.swift) 将全部文件读写、LRU 统计与淘汰统一收口到专用串行 `DispatchQueue`（`.utility` QoS），热路径写缓存采用异步方法脱离 `@MainActor`。

## Considered Options

- **全原生 SwiftUI 阅读器 + 本地解析引擎**：彻底绕开 WebView 与 Scheme 代理，但需在 Swift 中重新维护排版、双语翻译、MOBI/AZW3 与 PDF 视窗，且 Web 新特性无法同步下发。
- **使用浏览器 Service Worker 离线方案**：无法在局域网非 HTTPS 环境下注册，且无法像原生产生独立的 `Keychain` 口令隔离与确定性的整本磁盘配额清理。

## Consequences

- 服务端静态/封面缓存策略与 iOS 端自然协同；快速翻页取消请求不会触发假离线提示或阻塞主线程 UI。
- 代理协议仍对历史备选地址发探测且 `Info.plist` 为兼容局域网 HTTP 开启全局 `NSAllowsArbitraryLoads`，这两项收敛保留在第四批安全工作包（4A）中进一步收紧。
