#!/usr/bin/env bash
# Builds the native macOS helpers when missing or out of date. Run from anywhere; used by start.sh and install.sh.
set -e
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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
