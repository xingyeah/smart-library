# iPhone app

WKWebView shell around `frontend/index.html` with a native caching proxy for offline reading.
Design and decisions: `docs/designs/ios-app.md`.

## Build

Needs Xcode 16+ (the project uses synchronized folders, objectVersion 77).

```bash
sudo xcode-select -s /Applications/Xcode.app
xcodebuild -project ios/Library.xcodeproj -scheme Library -destination 'platform=iOS Simulator,name=iPhone 16' build
```

In the Simulator connect to `http://127.0.0.1:8765` (loopback needs no token). On a phone paste the LAN
address the server prints, with its `?token=`. Installing on a phone needs a signing team set in Xcode.

## Self-test (no Xcode needed)

Builds the proxy/offline sources for macOS and checks download, offline answers and progress replay
against the local server:

```bash
ios/SelfTest/run.sh [epubBookId] [pdfBookId]
```
