# Design: Android app for the library (WebView shell + offline cache proxy)

Status: APPROVED (grilled and confirmed 2026-11-23)
Mirrors: `docs/designs/ios-app.md`. Where this file is silent, the iOS behaviour applies.

## Problem Statement
The iPhone app wraps `frontend/index.html` in a WKWebView with a native caching proxy. The Android app
must offer the same features with the same architecture, so web changes reach both apps with no rebuild.

## Decisions
- **Architecture: Kotlin + WebView shell.** Native code only does server connection, the auth proxy and
  offline storage. Rejected: Flutter / React Native (second UI stack), native reader rewrite.
- **Page origin: `https://library.app/`.** A fake host that never reaches the network; every request is
  answered by `WebViewClient.shouldInterceptRequest`, which forwards to the saved server with the
  `lib_token` cookie. Plays the role of `library://app/` on iOS.
- **Native bridge without frontend changes.** A script injected at document start defines
  `window.webkit.messageHandlers.library.postMessage` on top of an `addJavascriptInterface` object, so
  `frontend/assets/js/main.js` keeps calling the iOS contract. It also sets `window.libraryAppOffline`
  (Set of downloaded book ids) and `window.libraryAppIsOffline`, and calls `libraryAppSetOffline`.
- **POST requests.** `shouldInterceptRequest` cannot read request bodies. The same injected script wraps
  `window.fetch`: non-GET requests go to the native bridge, which forwards them, caches translation
  responses by body hash, queues progress while offline, and resolves the page's promise. The frontend
  has no XHR, so `fetch` covers every POST.
- **Offline store.** Same layout as iOS under `filesDir/Offline`: `shared/`, `books/<id>/`, `cache/<id>/`,
  `pending-progress/<id>`; hashed file names; one single-thread executor for all file I/O; read-through
  cache capped at 500 MB with whole-book LRU eviction. No Room.
- **Downloads** run only while the app is in the foreground, with a progress banner. No foreground service.
- **Dependencies:** Kotlin, AndroidX (Compose), coroutines. Networking with `HttpURLConnection`.
  No OkHttp. The token is encrypted with an Android Keystore AES-GCM key and kept in SharedPreferences
  (`security-crypto` is deprecated).
- **Connect:** paste `http://<ip>:8765/?token=…`. Several addresses are tried (LAN before Tailscale),
  re-probed on `ConnectivityManager` network callbacks and when the app returns to the foreground.
- **Back:** system back key calls `webView.goBack()` when `canGoBack()`, otherwise leaves the app.
- **Distribution:** personal use, self-signed APK via `adb install`. `minSdk 26`, phones only,
  cleartext HTTP allowed (Tailscale encrypts that hop). `applicationId local.library.reader`,
  app name `我的图书馆`, adaptive icon converted from `ios/design/app-icon.svg`.

## Feature parity checklist
Connect page · encrypted token · multi-address probing and sync · auth proxy with ETag / Cache-Control
passthrough and cancel-without-offline-fallback · offline download (EPUB / MOBI chapters, PDF pages and
text, covers, detail JSON) · offline shelf · progress replay · translation cache · 503 JSON for uncached
endpoints · settings (cache size, delete downloads, change server) · launch status · download banner ·
offline / reconnected notices · JS alert / confirm / prompt dialogs · dark theme matching `--bg`.

## Constraints
- Server endpoints and token gate stay as they are. `frontend/` is not modified.
- Functions ≤ 50 lines, files ≤ 500 lines (same repo gate as the Swift files).
- Work stays inside `android/` and `docs/`. Signing keys and `local.properties` are git-ignored.

## Phases
- **A** — project skeleton, connect page, auth proxy, dark theme, launch status.
- **B** — offline store, downloader, offline shelf, progress replay, POST cache.
- **C** — settings sheet, notices, back key, icon.
Each phase is verified in the emulator (server reached at `10.0.2.2:8765`) and committed on its own.

## Verification
- JVM unit tests (`gradlew test`): `ServerConfig.parse`, cache key, `assetPaths`, store read/write,
  eviction, progress replay.
- Emulator end-to-end: connect, read EPUB and PDF, download a book, stop the server, relaunch, read
  offline, restart the server and check progress reaches it.
