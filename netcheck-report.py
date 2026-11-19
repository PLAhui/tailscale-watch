#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""汇总 netcheck-log.csv —— 中文报表
   ./netcheck-report.py           全部
   ./netcheck-report.py --today   只看今天
   ./netcheck-report.py --tail N  最近 N 条明细
"""
import csv, os, sys, unicodedata
from datetime import datetime

DIR = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(DIR, "netcheck-log.csv")

ZH_REGION = {
    "Los Angeles": "洛杉矶", "San Francisco": "旧金山", "Seattle": "西雅图",
    "Denver": "丹佛", "Dallas": "达拉斯", "Nuremberg": "纽伦堡",
    "Helsinki": "赫尔辛基", "Hong Kong": "香港", "Tokyo": "东京",
    "Singapore": "新加坡", "New York City": "纽约", "Miami": "迈阿密",
    "Toronto": "多伦多", "Chicago": "芝加哥", "Amsterdam": "阿姆斯特丹",
    "Frankfurt": "法兰克福", "Ashburn": "阿什本", "London": "伦敦",
    "Paris": "巴黎", "Madrid": "马德里", "Warsaw": "华沙",
    "Sydney": "悉尼", "Johannesburg": "约翰内斯堡", "Dubai": "迪拜",
    "Nairobi": "内罗毕", "São Paulo": "圣保罗", "Sao Paulo": "圣保罗",
    "Mumbai": "孟买", "Honolulu": "檀香山",
}


def w(s):
    """显示宽度：中日韩全角按 2 算"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(s))


def pad(s, width):
    s = str(s)
    return s + " " * max(0, width - w(s))


def num(v, idx=1):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def zh_region(v):
    return ZH_REGION.get(v, v)


def zh_path(v):
    if v == "direct":
        return "直连"
    if v.startswith("relay_"):
        return "中继/" + v[6:]
    if v == "offline":
        return "离线"
    if v == "unknown":
        return "未知"
    return v


def table(rows, headers, aligns):
    widths = [w(h) for h in headers]
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], w(c))
    line = "  ".join(pad(h, widths[i]) for i, h in enumerate(headers)).rstrip()
    out = [line, "  ".join("-" * x for x in widths)]
    for r in rows:
        cells = []
        for i, c in enumerate(r):
            cells.append(pad(c, widths[i]) if aligns[i] == "l" else " " * (widths[i] - w(c)) + str(c))
        out.append("  ".join(cells).rstrip())
    return "\n".join(out)


def load():
    if not os.path.exists(LOG):
        sys.exit("还没有日志: " + LOG)
    with open(LOG, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    args = sys.argv[1:]
    rows = load()
    if not rows:
        sys.exit("日志还是空的，等一下再跑")

    if args and args[0] == "--today":
        today = datetime.now().strftime("%Y-%m-%d")
        rows = [r for r in rows if r["time"].startswith(today)]
        if not rows:
            sys.exit("今天还没有样本")

    if args and args[0] == "--tail":
        n = int(args[1]) if len(args) > 1 else 20
        rows = rows[-n:]
        data = [[r["time"].split(" ")[1], zh_region(r["nearest_derp"]), r["derp_fastest"],
                 r["derp_fastest_ms"], r["derp_reachable"], r["ali_loss"], r["ali_avg"],
                 r["ali_max"], r["macmini_avg"], r["macmini_max"],
                 r.get("gateway_avg") or "—", r.get("gateway_max") or "—", zh_path(r["path"])] for r in rows]
        print("== 最近 %d 条明细 ==" % len(rows))
        print(table(data,
                    ["采样时间", "备用中继", "最快中继", "中继延迟(ms)", "可达数", "阿里丢包(%)",
                     "阿里平均(ms)", "阿里峰值(ms)", "mac-mini 平均(ms)", "mac-mini 峰值(ms)",
                     "网关平均(ms)", "网关峰值(ms)", "实际路径"],
                    ["l", "l", "l", "r", "r", "r", "r", "r", "r", "r", "r", "r", "l"]))
        return

    # ---- 总体 ----
    ali = [num(r["ali_avg"]) for r in rows if num(r["ali_avg"]) is not None]
    ali_max = [num(r["ali_max"]) for r in rows if num(r["ali_max"]) is not None]
    mm = [num(r["macmini_avg"]) for r in rows if num(r["macmini_avg"]) is not None]
    mm_max = [num(r["macmini_max"]) for r in rows if num(r["macmini_max"]) is not None]
    derp = [num(r["derp_fastest_ms"]) for r in rows if num(r["derp_fastest_ms"]) is not None]
    gw = [num(r.get("gateway_avg")) for r in rows if num(r.get("gateway_avg")) is not None]
    loss = sum(1 for r in rows if (num(r["ali_loss"]) or 0) > 0 or (num(r["macmini_loss"]) or 0) > 0)
    direct = sum(1 for r in rows if r["path"] == "direct")
    relay = sum(1 for r in rows if r["path"].startswith("relay"))
    reach_bad = sum(1 for r in rows if (num(r["derp_reachable"]) or 99) < 20)

    def s(lst):
        return "%.0f / %.0f / %.0f" % (min(lst), sum(lst) / len(lst), max(lst)) if lst else "-"

    print("== 总体 ==")
    info = [
        ["样本数", "%d 条（%s ~ %s）" % (len(rows), rows[0]["time"], rows[-1]["time"])],
        ["路由器网关延迟（内网基准）", "%s ms（最小/平均/最大）" % s(gw)],
        ["阿里 DNS 延迟（国内基准）", "%s ms（最小/平均/最大）" % s(ali)],
        ["阿里 峰值 >150ms 次数", "%d 次" % sum(1 for x in ali_max if x > 150)],
        ["mac-mini 延迟", "%s ms（最小/平均/最大）" % s(mm)],
        ["中继最快延迟", "%s ms（最小/平均/最大）" % s(derp)],
        ["出现丢包的样本", "%d 条" % loss],
        ["实际路径", "直连 %d 条 / 中继 %d 条" % (direct, relay)],
        ["可达中继 <20 的样本", "%d 条" % reach_bad],
    ]
    print(table(info, ["指标", "数值"], ["l", "l"]))
    print()

    # ---- 按小时 ----
    buckets = {}
    for r in rows:
        h = r["time"][:13]
        b = buckets.setdefault(h, {"n": 0, "ali": [], "amax": [], "mm": [], "direct": 0, "relay": 0,
                                   "derp": [], "gw": []})
        b["n"] += 1
        if num(r["ali_avg"]) is not None:
            b["ali"].append(num(r["ali_avg"]))
        if num(r["ali_max"]) is not None:
            b["amax"].append(num(r["ali_max"]))
        if num(r["macmini_avg"]) is not None:
            b["mm"].append(num(r["macmini_avg"]))
        if num(r["derp_fastest_ms"]) is not None:
            b["derp"].append(num(r["derp_fastest_ms"]))
        if num(r.get("gateway_avg")) is not None:
            b.setdefault("gw", []).append(num(r.get("gateway_avg")))
        if r["path"] == "direct":
            b["direct"] += 1
        else:
            b["relay"] += 1

    data = []
    for h in sorted(buckets):
        b = buckets[h]
        data.append([h[-2:] + "点", str(b["n"]),
                     "%.0f" % (sum(b["ali"]) / len(b["ali"])) if b["ali"] else "-",
                     "%.0f" % max(b["amax"]) if b["amax"] else "-",
                     "%.0f" % (sum(b["mm"]) / len(b["mm"])) if b["mm"] else "-",
                     "%.0f" % (sum(b["derp"]) / len(b["derp"])) if b["derp"] else "-",
                     "%.0f" % (sum(b["gw"]) / len(b["gw"])) if b["gw"] else "-",
                     str(b["direct"]), str(b["relay"])])
    print("== 按小时 ==")
    print(table(data, ["时段", "样本数", "阿里平均(ms)", "阿里峰值(ms)", "mac-mini 平均(ms)",
                       "中继延迟(ms)", "网关平均(ms)", "直连次数", "中继次数"],
                ["l", "r", "r", "r", "r", "r", "r", "r", "r"]))
    print()
    print("提示：阿里平均 >40ms 且最大值频繁翻倍 = 上行拥塞；中继延迟各时段几乎相同 = 队列延迟主导。")


if __name__ == "__main__":
    main()
