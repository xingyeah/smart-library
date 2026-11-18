#!/usr/bin/env bash
set -e
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${REPO_DIR}"

bash scripts/build_native.sh

# API keys written by install.sh (never committed: local/ is ignored)
if [ -f local/.env ]; then
  set -a
  . local/.env
  set +a
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
