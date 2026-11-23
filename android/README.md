# Android app

WebView shell around `frontend/index.html` with a native caching proxy for offline reading.
Design and decisions: `docs/designs/android-app.md` and `docs/adr/0005-android-webview-shell-and-fetch-bridge-for-post.md`.
It mirrors the iPhone app in `ios/`.

## Build

Needs JDK 17+ and the Android SDK (`ANDROID_HOME`, with `platforms;android-35` and `build-tools;35.0.0`).

```bash
cd android
export ANDROID_HOME=/opt/homebrew/share/android-commandlinetools
./gradlew assembleDebug        # app/build/outputs/apk/debug/app-debug.apk
./gradlew test                 # JVM unit tests
```

In the emulator run `adb reverse tcp:8765 tcp:8765` and connect to `http://127.0.0.1:8765`: the server then
sees a loopback client, which needs no token (`http://10.0.2.2:8765` arrives as a LAN client and does). On a
phone paste the LAN address the server prints, with its `?token=`.

Install on a phone with `adb install -r app/build/outputs/apk/debug/app-debug.apk`.
