# 0005：Android 采用 WebView 外壳，并用注入的 fetch 桥接处理 POST

- Status: accepted

## Context

Android 端要复用与 iOS 相同的 Web 阅读器、离线下载和阅读进度回放。局域网是 HTTP，Service Worker 不可用，令牌也不应进入 Web 层。Android 的 `WebViewClient.shouldInterceptRequest` 能拦截 GET，但读不到请求体，而翻译、进度上报、整理等接口都是 POST。

## Decision

1. **架构**：Kotlin 加 WebView 外壳，页面为 `https://library.app/`（不发真实网络请求的假域名），所有请求由 `shouldInterceptRequest` 转发到已保存的服务器并加 `lib_token` cookie，与 [ADR 0004](0004-ios-scheme-proxy-and-serialized-offline-store.md) 的缓存协议、取消语义、串行化存储一一对应。
2. **POST 经桥接**：文档开始时注入脚本，改写 `window.fetch`，非 GET 请求交给 `addJavascriptInterface` 对象，由原生转发、缓存并回传结果。前端无 XHR，因此覆盖全部 POST。
3. **桥接契约不变**：注入脚本在 `window.webkit.messageHandlers.library.postMessage` 上适配原生对象，`frontend/` 不改。
4. **依赖最小**：Kotlin、AndroidX、协程；网络用 `HttpURLConnection`；令牌用 Keystore AES-GCM 加密后存 SharedPreferences。

## Considered Options

- **本地 127.0.0.1 HTTP 服务器**：能让页面发出真实 POST，但要手写服务器并常驻后台线程，代码量约翻倍。
- **Flutter / React Native / 原生重写阅读器**：要维护第二套界面与排版，且 Web 新特性无法同步。
- **Room 数据库做离线存储**：与 iOS 的文件布局不一致，收益不抵多出的依赖。

## Consequences

- 前端改动会同时到达 iOS 与 Android，无需重新打包。
- POST 的行为由注入脚本保证，脚本版本需随前端 `fetch` 用法一起回归（见 `docs/designs/android-app.md` 的验证清单）。
- 允许明文 HTTP 是为了局域网和 Tailscale，后续收紧范围与 iOS 的 4A 工作包一并处理。
