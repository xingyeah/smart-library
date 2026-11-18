#!/usr/bin/env bash
set -e
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${REPO_DIR}"

if [ ! -f "backend/native/libmacbookai.dylib" ] || [ backend/native/libmacbookai.m -nt backend/native/libmacbookai.dylib ]; then
  echo "Compiling native macOS acceleration engine..."
  clang -dynamiclib -O3 -fobjc-arc \
    -framework Foundation -framework Vision -framework PDFKit -framework NaturalLanguage -framework AppKit \
    backend/native/libmacbookai.m -o backend/native/libmacbookai.dylib
fi

if [ ! -f "backend/native/apple_translate" ] && command -v swiftc >/dev/null; then
  echo "Compiling on-device translation helper (macOS 26 Translation framework)..."
  swiftc -O -parse-as-library backend/native/apple_translate.swift -o backend/native/apple_translate || echo "  skipped: Apple translation needs macOS 26"
fi

# caffeinate -s keeps the Mac awake while plugged in, so the phone can reach the server; on battery it sleeps as usual
PORT="${1:-8765}"
LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo '')"

echo "=================================================="
echo "🚀 启动我的智能图书馆系统 (0.0.0.0:${PORT})..."
echo "   - 本机访问:  http://127.0.0.1:${PORT}"
if [ -n "${LAN_IP}" ]; then
  echo "   - 局域网访问: http://${LAN_IP}:${PORT}"
fi
echo "=================================================="

if [ -x "${REPO_DIR}/.venv/bin/python" ]; then
  exec caffeinate -s "${REPO_DIR}/.venv/bin/python" backend/server.py "${PORT}" "0.0.0.0"
else
  exec caffeinate -s /usr/bin/python3 backend/server.py "${PORT}" "0.0.0.0"
fi
