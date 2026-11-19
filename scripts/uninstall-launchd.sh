#!/bin/bash
# 卸载 launchd 服务（保留数据文件）
set -uo pipefail
LA="$HOME/Library/LaunchAgents"

for svc in agent panel; do
  label="io.github.tailscale-watch.${svc}"
  launchctl bootout "gui/$(id -u)/${label}" 2>/dev/null || true
  rm -f "${LA}/${label}.plist"
  echo "已卸载 ${label}"
done
