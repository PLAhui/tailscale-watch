#!/bin/bash
# 兼容旧命令：转调 netcheck-report.py（中文报表）
#   ./netcheck-report.sh            全部汇总
#   ./netcheck-report.sh --today    只看今天
#   ./netcheck-report.sh --tail 30  最近 30 条明细
DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$DIR/netcheck-report.py" "$@"
