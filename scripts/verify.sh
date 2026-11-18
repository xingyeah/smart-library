#!/usr/bin/env bash
# One-command check before merging or releasing: Python tests, frontend gate, iOS type-check.
# Tests that need real books skip themselves when the sample is missing, so a green run needs no private data.
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PYTHON=".venv/bin/python"
IOS_SOURCES=(ServerConfig ServerClient OfflineStore OfflineResponder LibrarySchemeHandler BookDownloader)

step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
fail() { printf '\n\033[31m✗ %s\033[0m\n' "$1" >&2; exit 1; }

[ -x "$PYTHON" ] || fail "没有 .venv，先运行 ./install.sh"

step "1/3 Python 测试（含代码规范与架构门禁）"
"$PYTHON" -m unittest discover -s tests || fail "Python 测试未通过"

step "2/3 前端检查（语法 + checkJs）"
command -v node >/dev/null || fail "缺少 node"
node scripts/check_frontend_js.mjs --require-ts || fail "前端检查未通过"

step "3/3 iOS 非 UI 源码类型检查"
if ! command -v swiftc >/dev/null; then
  printf '  跳过：没有 swiftc（需要 Xcode 命令行工具）\n'
else
  swift_files=()
  for name in "${IOS_SOURCES[@]}"; do swift_files+=("ios/Library/${name}.swift"); done
  swiftc -typecheck -parse-as-library -swift-version 5 -target arm64-apple-macosx14.0 \
    "${swift_files[@]}" ios/SelfTest/main.swift || fail "iOS 类型检查未通过"
fi

printf '\n\033[32m✓ 全部通过\033[0m\n'
