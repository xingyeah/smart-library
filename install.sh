#!/usr/bin/env bash
# Interactive setup for this library on macOS. Safe to run again: finished steps are skipped or offered for change.
# Usage: ./install.sh    (set HF_ENDPOINT, e.g. https://hf-mirror.com, if Hugging Face is slow or blocked)
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# requirements.txt pins wheels (numpy 2.0, onnxruntime 1.19) that exist for Python 3.9-3.12 only
MIN_PYTHON_MINOR=9
MAX_PYTHON_MINOR=12
PYTHON=""
VENV_PY=".venv/bin/python"
CONFIG_FILE="local/config.json"
ENV_FILE="local/.env"
DEFAULT_SOURCE="books/inbox"

say()  { printf '\n\033[1m%s\033[0m\n' "$1"; }
info() { printf '  %s\n' "$1"; }
fail() { printf '\n\033[31m✗ %s\033[0m\n' "$1" >&2; exit 1; }

# ask_yes_no "问题" y|n  -> exit status 0 for yes
ask_yes_no() {
  local hint="[Y/n]"; [ "$2" = "n" ] && hint="[y/N]"
  local reply; read -r -p "  $1 $hint " reply
  reply="${reply:-$2}"
  [[ "$reply" =~ ^[Yy] ]]
}

# ask "问题" "默认值" -> prints the answer
ask() {
  local reply; read -r -p "  $1 [$2] " reply
  printf '%s' "${reply:-$2}"
}

check_environment() {
  say "1/7 检查环境"
  [ "$(uname -s)" = "Darwin" ] || fail "只支持 macOS（原生加速库用到 PDFKit、Vision 等系统框架）。"
  command -v clang >/dev/null || fail "缺少 Xcode 命令行工具。先运行：xcode-select --install，装好后重新运行本脚本。"
  pick_python || fail "需要 Python 3.${MIN_PYTHON_MINOR}–3.${MAX_PYTHON_MINOR}。可以安装：brew install python@3.12"
  info "macOS、clang、$("$PYTHON" --version)（${PYTHON}）都已就绪"
}

# Newest Python in the supported range; Xcode's /usr/bin/python3 (3.9) is the fallback every Mac with clang has
pick_python() {
  local candidate minor
  for candidate in python3.12 python3.11 python3.10 python3 /usr/bin/python3; do
    command -v "$candidate" >/dev/null || continue
    minor="$("$candidate" -c 'import sys; print(sys.version_info.minor if sys.version_info.major == 3 else 0)' 2>/dev/null)" || continue
    if [ "$minor" -ge "$MIN_PYTHON_MINOR" ] && [ "$minor" -le "$MAX_PYTHON_MINOR" ]; then
      PYTHON="$(command -v "$candidate")"
      return 0
    fi
  done
  return 1
}

install_python_deps() {
  say "2/7 安装 Python 依赖"
  [ -x "$VENV_PY" ] || { info "创建虚拟环境 .venv"; "$PYTHON" -m venv .venv; }
  "$VENV_PY" -m pip install --quiet --disable-pip-version-check --upgrade pip
  "$VENV_PY" -m pip install --quiet --disable-pip-version-check -r requirements.txt
  info "依赖已安装"
}

build_native() {
  say "3/7 编译原生加速库"
  bash scripts/build_native.sh
  info "完成"
}

collect_source_dirs() {
  info "输入要扫描的电子书目录，一行一个，直接回车结束。都不填就用 ${DEFAULT_SOURCE}/。" >&2
  local dirs=() dir
  while true; do
    read -r -p "  目录: " dir
    [ -z "$dir" ] && break
    dir="${dir/#\~/$HOME}"
    if [ -d "$dir" ]; then dirs+=("$dir"); else info "找不到目录：$dir，已跳过" >&2; fi
  done
  if [ "${#dirs[@]}" -eq 0 ]; then mkdir -p "$DEFAULT_SOURCE"; dirs=("$DEFAULT_SOURCE"); fi
  printf '%s\n' "${dirs[@]}"
}

write_config() {
  say "4/7 配置书源和存放位置"
  mkdir -p local data
  if [ -f "$CONFIG_FILE" ] && ask_yes_no "已有 ${CONFIG_FILE}，保留现有配置？" y; then
    info "保留现有配置"
  else
    local sources library_dir db_path
    sources="$(collect_source_dirs)"
    library_dir="$(ask "归档后的书放在哪里" "Library")"
    db_path="$(ask "书库数据库放在哪里" "data/library.db")"
    SOURCES="$sources" LIBRARY_DIR="$library_dir" DB_PATH="$db_path" "$VENV_PY" - "$CONFIG_FILE" <<'PY'
import json, os, sys
paths = [os.path.abspath(p) for p in os.environ["SOURCES"].splitlines() if p.strip()]
sources = [{"path": p, "name": os.path.basename(p.rstrip("/")) or p, "trust_weight": 1.0, "priority": i + 1}
           for i, p in enumerate(paths)]
config = {"source_dirs": sources, "library_dir": os.environ["LIBRARY_DIR"], "db_path": os.environ["DB_PATH"]}
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(config, f, ensure_ascii=False, indent=2)
PY
    info "已写入 ${CONFIG_FILE}"
  fi
  if command -v git >/dev/null && [ ! -d local/.git ] && ask_yes_no "用 git 在 local/ 里记录配置的变化（只在本机，不推送）？" y; then
    printf '.env\n' > local/.gitignore
    git -C local init -q && git -C local add -A && git -C local commit -q -m "chore: library config" && info "local/ 已建成本地仓库"
  fi
}

write_api_keys() {
  say "5/7 API 密钥（都可以跳过，跳过后对应的 AI 功能不可用）"
  if [ -f "$ENV_FILE" ] && ask_yes_no "已有 ${ENV_FILE}，保留现有密钥？" y; then
    info "保留现有密钥"
    return
  fi
  local name label value lines=""
  for entry in "DEEPSEEK_API_KEY|DeepSeek（AI 精排、翻译、书目整理）" \
               "ANTHROPIC_API_KEY|Anthropic（Claude 翻译）" \
               "GOOGLE_BOOKS_API_KEY|Google Books（评分和简介）"; do
    name="${entry%%|*}"; label="${entry#*|}"
    read -r -s -p "  ${label}，直接回车跳过: " value; echo
    [ -n "$value" ] && lines+="$(printf '%s=%q' "$name" "$value")"$'\n'
  done
  ( umask 077; printf '%s' "$lines" > "$ENV_FILE" )
  info "已写入 ${ENV_FILE}（仅你可读，不进 git），start.sh 启动时会加载"
}

download_models() {
  say "6/7 语义搜索模型（约 1.1 GB）"
  local m3="data/models/bge-m3/onnx/sentence_transformers_quantized.onnx"
  local rr="data/models/bge-reranker-v2-m3/onnx/model_quantized.onnx"
  if [ -f "$m3" ] && [ -f "$rr" ]; then info "模型已就绪"; return; fi
  if ! ask_yes_no "从 Hugging Face 下载？不下载的话，搜索只用关键词。" y; then
    info "已跳过，以后重新运行本脚本即可下载"
    return
  fi
  # File names must match backend/semantic_models.py
  "$VENV_PY" - <<'PY' || fail "模型下载失败。网络不通时可以换镜像重试：HF_ENDPOINT=https://hf-mirror.com ./install.sh"
from huggingface_hub import hf_hub_download
for repo, target, files in (
    ("Xenova/bge-m3", "data/models/bge-m3", ("onnx/sentence_transformers_quantized.onnx", "tokenizer.json")),
    ("onnx-community/bge-reranker-v2-m3-ONNX", "data/models/bge-reranker-v2-m3", ("onnx/model_quantized.onnx", "tokenizer.json")),
):
    for name in files:
        print(f"  下载 {repo}/{name}", flush=True)
        hf_hub_download(repo_id=repo, filename=name, local_dir=target)
PY
  info "模型已下载"
}

first_organize() {
  say "7/7 首次整理"
  info "扫描书源、识别书目、去重归档并建立搜索索引。书多时要很久，也可以以后在网页「整理」页再做。"
  info "配了 DeepSeek 密钥时，整理会调用它补全书目信息，产生少量费用。"
  ask_yes_no "现在开始整理？" y || { info "已跳过"; return; }
  if [ -f "$ENV_FILE" ]; then set -a; . "$ENV_FILE"; set +a; fi
  "$VENV_PY" backend/cli.py organize
}

finish() {
  say "安装完成"
  info "启动：./start.sh   （默认端口 8765）"
  info "本机访问：http://127.0.0.1:8765"
  info "手机和平板用启动时打印的局域网地址，口令在 data/lan_token"
  info "iPhone App 需要用 Xcode 自己编译安装，见 ios/README.md"
}

check_environment
install_python_deps
build_native
write_config
write_api_keys
download_models
first_organize
finish
