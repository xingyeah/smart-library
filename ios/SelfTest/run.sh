#!/bin/sh
# Builds the app's non-UI sources with the self-test for macOS and runs it against http://127.0.0.1:8765.
set -e
here=$(cd "$(dirname "$0")" && pwd)
out="${TMPDIR:-/tmp}/library-selftest"
src="$here/../Library"
swiftc -parse-as-library -swift-version 5 -target arm64-apple-macosx14.0 -o "$out" \
  "$src/ServerConfig.swift" "$src/ServerClient.swift" "$src/OfflineStore.swift" "$src/OfflineResponder.swift" \
  "$src/LibrarySchemeHandler.swift" "$src/BookDownloader.swift" "$here/main.swift"
"$out" "$@"
