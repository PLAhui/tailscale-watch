#!/bin/bash
# Tailscale + 链路质量采样（含路由器网关基准）
#   ./netcheck-watch.sh --once    采样一次
#   ./netcheck-watch.sh --daemon  每 INTERVAL 秒采样（前台，Ctrl-C 停）
#   ./netcheck-watch.sh --bg      后台常驻
#   ./netcheck-watch.sh --stop    停掉后台循环
# 环境变量: INTERVAL=秒(默认60) PING_COUNT=次(默认10)

set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$DIR/netcheck-log.csv"
PIDFILE="$DIR/.watch.pid"
TS="/Applications/Tailscale.app/Contents/MacOS/tailscale"
# 读取本机配置（config.env 不入库，见 config.env.example）
[ -f "$DIR/config.env" ] && . "$DIR/config.env"
MACMINI_IP="${PEER_HOST:-}"          # 对端 Tailscale 地址，留空则这一列记空
ALI_DNS="${ALI_HOST:-223.5.5.5}"
INTERVAL="${INTERVAL:-60}"
PING_COUNT="${PING_COUNT:-10}"

ts_run() {
  # 注意: 凭据不可用时 CLI 会以 exit 0 把 "Error: CLI credentials are not
  # available" 打到 **stdout**，所以不能用退出码判断，只能看内容。
  local o
  o=$("$TS" "$@" 2>/dev/null)
  case "$o" in
    ""|"Error:"*) : ;;
    *) printf '%s\n' "$o"; return 0 ;;
  esac
  sudo -n "$TS" "$@" 2>/dev/null
}

# 从 ping 输出里取 loss / avg / max
ping_field() {  # $1=输出 $2=loss|avg|max
  case "$2" in
    loss) printf '%s' "$1" | sed -nE 's/.* ([0-9.]+)% packet loss.*/\1/p' | head -1 ;;
    avg|max)
      local idx=2; [ "$2" = "max" ] && idx=3
      printf '%s' "$1" | awk -v i="$idx" -F'=' '/min\/avg\/max/ {gsub(/ ms/,"",$2); split($2,a,"/"); print a[i]}' | tail -1 ;;
  esac
}

gateway_ip() {
  route -n get default 2>/dev/null | awk '/gateway:/ {print $2; exit}'
}

sample() {
  local now out nearest derp_best derp_best_ms reachable ali mm gw gw_ip path
  local ali_loss ali_avg ali_max mm_loss mm_avg mm_max gw_loss gw_avg gw_max

  now=$(date '+%Y-%m-%d %H:%M:%S')

  # 1) netcheck
  out=$(ts_run netcheck)
  nearest=$(printf '%s\n' "$out" | sed -n 's/.*Nearest DERP: *//p' | head -1 | tr -d '\r')
  reachable=$(printf '%s\n' "$out" | awk '/ms \(/ {c++} END {print c+0}')
  derp_best=$(printf '%s\n' "$out" \
    | awk '/ms \(/ {sub(/^[ \t*-]+/,""); print}' \
    | sed -E 's/^([a-z0-9]+): *([0-9.]+)ms.*/\1 \2/' \
    | sort -k2 -n | head -1)
  derp_best_ms=$(printf '%s' "$derp_best" | awk '{print $2}')
  derp_best=$(printf '%s' "$derp_best" | awk '{print $1}')

  # 2) 三段 ping：国内基准 / 对端 / 路由器
  ali=$(ping -c "$PING_COUNT" -i 0.2 -t 5 "$ALI_DNS" 2>/dev/null)
  if [ -n "$MACMINI_IP" ]; then
    mm=$(ping -c "$PING_COUNT" -i 0.2 -t 5 "$MACMINI_IP" 2>/dev/null)
  else
    mm=""
  fi
  gw_ip=$(gateway_ip)
  if [ -n "${gw_ip:-}" ]; then
    gw=$(ping -c 5 -i 0.2 -t 3 "$gw_ip" 2>/dev/null)
  else
    gw=""; gw_ip="NA"
  fi

  # 3) 当前路径 direct / relay
  local st; st=$(ts_run status)
  if printf '%s' "$st" | grep -q 'mac-mini'; then
    if printf '%s' "$st" | grep 'mac-mini' | grep -q ' direct '; then path="direct"
    else path=$(printf '%s' "$st" | grep 'mac-mini' | grep -o 'relay "[a-z]*"' | head -1 | tr -d '"' | tr ' ' '_')
         [ -z "$path" ] && path="offline"
    fi
  else path="unknown"; fi

  ali_loss=$(ping_field "$ali" loss); ali_avg=$(ping_field "$ali" avg); ali_max=$(ping_field "$ali" max)
  mm_loss=$(ping_field "$mm" loss);   mm_avg=$(ping_field "$mm" avg);   mm_max=$(ping_field "$mm" max)
  gw_loss=$(ping_field "$gw" loss);   gw_avg=$(ping_field "$gw" avg);   gw_max=$(ping_field "$gw" max)

  [ -f "$LOG" ] || echo "time,nearest_derp,derp_fastest,derp_fastest_ms,derp_reachable,ali_loss,ali_avg,ali_max,macmini_loss,macmini_avg,macmini_max,path,gateway_loss,gateway_avg,gateway_max,gateway_ip" > "$LOG"
  printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
    "$now" "${nearest:-NA}" "${derp_best:-NA}" "${derp_best_ms:-NA}" "$reachable" \
    "${ali_loss:-NA}" "${ali_avg:-NA}" "${ali_max:-NA}" \
    "${mm_loss:-NA}" "${mm_avg:-NA}" "${mm_max:-NA}" \
    "$path" \
    "${gw_loss:-NA}" "${gw_avg:-NA}" "${gw_max:-NA}" "${gw_ip:-NA}" >> "$LOG"

  printf '[%s] 阿里 %sms | mac-mini %sms | 网关 %sms | 中继 %s@%sms | 路径 %s\n' \
    "$now" "${ali_avg:-NA}" "${mm_avg:-NA}" "${gw_avg:-NA}" "${derp_best:-NA}" "${derp_best_ms:-NA}" "$path"
}

case "${1:---once}" in
  --once) sample ;;
  --daemon)
    [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null && { echo "已在运行 (PID $(cat "$PIDFILE"))"; exit 1; }
    echo $$ > "$PIDFILE"
    trap 'rm -f "$PIDFILE"; exit 0' INT TERM
    echo "开始采样，每 ${INTERVAL}s 一次，日志: $LOG （Ctrl-C 结束）"
    while true; do sample; sleep "$INTERVAL"; done ;;
  --bg) nohup "$0" --daemon >/dev/null 2>&1 & echo "后台已启动 PID $!" ;;
  --stop)
    if [ -f "$PIDFILE" ]; then kill "$(cat "$PIDFILE")" 2>/dev/null && echo "已停止"; rm -f "$PIDFILE"; else echo "没有在跑"; fi ;;
  *) echo "用法: $0 [--once|--daemon|--bg|--stop]"; exit 1 ;;
esac
