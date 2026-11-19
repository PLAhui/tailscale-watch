#!/bin/bash
# 双击启动监控面板（本地临时运行；长期常驻请用 scripts/install-launchd.sh）
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PANEL_PORT:-8767}"
mkdir -p var

if curl -sf -m 2 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
  echo "服务已在运行 -> http://127.0.0.1:${PORT}"
  open "http://127.0.0.1:${PORT}/"
  exit 0
fi

echo "启动本地服务（端口 ${PORT}）…"
nohup /usr/bin/python3 app/server.py "${PORT}" > var/panel.log 2>&1 &
echo "${PORT}" > .panel-port

for _ in $(seq 1 30); do
  sleep 0.3
  if curl -sf -m 2 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
    open "http://127.0.0.1:${PORT}/"
    echo "面板地址: http://127.0.0.1:${PORT}"
    exit 0
  fi
done

echo "启动失败，最近日志："
tail -10 var/panel.log 2>/dev/null || echo "(无日志)"
exit 1
