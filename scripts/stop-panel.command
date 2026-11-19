#!/bin/bash
# 停止本项目的面板服务（只杀本仓库路径下的进程，避免误伤其他 Python 服务）
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIDS=$(pgrep -f "${ROOT}/app/server.py" 2>/dev/null || true)
if [ -n "${PIDS}" ]; then
  echo "${PIDS}" | xargs kill 2>/dev/null
  echo "已停止面板服务"
else
  echo "面板服务没有在跑"
fi
