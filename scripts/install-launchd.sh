#!/bin/bash
# 安装 launchd 服务：每 60 秒采样一次 + 面板常驻 8767
# 用法：bash scripts/install-launchd.sh [--port 8767]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LA="$HOME/Library/LaunchAgents"
PORT="${PANEL_PORT:-8767}"

while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    *) echo "未知参数: $1"; exit 1 ;;
  esac
done

mkdir -p "$ROOT/var" "$LA"

render() {
  local tpl="$1" out="$2"
  sed -e "s|__PROJECT_DIR__|$ROOT|g" "$tpl" > "$out"
}

for svc in agent panel; do
  plist="${LA}/io.github.tailscale-watch.${svc}.plist"
  launchctl bootout "gui/$(id -u)/io.github.tailscale-watch.${svc}" 2>/dev/null || true
  render "$ROOT/launchd/${svc}.plist.template" "$plist"
  launchctl bootstrap "gui/$(id -u)" "$plist"
  launchctl enable "gui/$(id -u)/io.github.tailscale-watch.${svc}" 2>/dev/null || true
  echo "已安装 io.github.tailscale-watch.${svc}"
done

# 面板端口以 plist 里的为准，同步写入 .panel-port
echo "$PORT" > "$ROOT/.panel-port"

sleep 2
if curl -sf -m 3 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
  echo "面板已就绪 -> http://127.0.0.1:${PORT}"
else
  echo "面板暂未响应，稍等几秒或看 $ROOT/var/panel.err"
fi

cat <<'TIP'

提示：如果你之前装过旧版（com.huiziqin.tailscale-*），建议先执行
  launchctl bootout gui/$(id -u)/com.huiziqin.tailscale-panel
  launchctl bootout gui/$(id -u)/com.huiziqin.tailscale-watch
避免两个实例抢同一个端口。
TIP
