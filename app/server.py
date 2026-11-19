#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tailscale 链路监控面板 —— 本地 HTTP 服务（仅使用 Python 标准库）

用法：
    python3 app/server.py            启动，默认 http://127.0.0.1:8767
    python3 app/server.py 9000       指定端口
    PANEL_PORT=9000 python3 app/server.py

只负责两件事：把 netcheck-log.csv 读成统计，把面板静态文件吐给浏览器。
所有耗时动作（采样、长 ping、AI 请求）都在后台线程里跑，不阻塞 HTTP 响应。
"""
import csv, json, os, re, subprocess, sys, threading
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(APP_DIR)
LOG = os.path.join(ROOT_DIR, "netcheck-log.csv")
WATCH = os.path.join(ROOT_DIR, "netcheck-watch.sh")
CONFIG = os.path.join(ROOT_DIR, "config.env")


def _load_config():
    """读取 config.env（键=值，# 注释）。仓库里只提交 config.env.example，
    真实的对端地址、网关等信息留在本机，不进版本库。"""
    cfg = {
        "PEER_HOST": "",            # 对端 Tailscale 地址（IP 或 MagicDNS 名）
        "PEER_NAME": "对端设备",
        "SELF_NAME": "",            # 留空则用系统 hostname
        "ALI_HOST": "223.5.5.5",
        "GATEWAY_FALLBACK": "",
    }
    if os.path.exists(CONFIG):
        try:
            for line in open(CONFIG, encoding="utf-8"):
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
        except Exception:
            pass
    for k in list(cfg):
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    return cfg


CFG = _load_config()

FIELDS = ["time", "nearest_derp", "derp_fastest", "derp_fastest_ms", "derp_reachable",
          "ali_loss", "ali_avg", "ali_max", "macmini_loss", "macmini_avg", "macmini_max",
          "path", "gateway_loss", "gateway_avg", "gateway_max", "gateway_ip"]

_sample_lock = threading.Lock()
_last_sample = {"at": None, "ok": None, "msg": ""}


def f(v):
    try:
        x = float(v)
        return x
    except (TypeError, ValueError):
        return None


def read_rows():
    if not os.path.exists(LOG):
        return []
    rows = []
    with open(LOG, newline="", encoding="utf-8") as fh:
        for rec in csv.reader(fh):
            if not rec or rec[0] == "time" or len(rec) < 12:
                continue
            r = dict(zip(FIELDS, rec + [""] * (len(FIELDS) - len(rec))))
            for k in ("derp_fastest_ms", "ali_avg", "ali_max", "macmini_avg", "macmini_max",
                      "gateway_avg", "gateway_max"):
                r[k] = f(r.get(k, ""))
            r["derp_reachable"] = f(r.get("derp_reachable")) or 0
            r["ali_loss"] = f(r.get("ali_loss"))
            r["macmini_loss"] = f(r.get("macmini_loss"))
            r["gateway_loss"] = f(r.get("gateway_loss"))
            rows.append(r)
    return rows


def slice_range(rows, rng):
    if rng == "all" or not rows:
        return rows
    now = datetime.now()
    if rng == "today":
        return [r for r in rows if r["time"].startswith(now.strftime("%Y-%m-%d"))]
    if rng == "6h":
        from datetime import timedelta
        cut = (now - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")
        return [r for r in rows if r["time"] >= cut]
    return rows


def avg(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 1) if xs else None


def pct(xs, q):
    """百分位（q 取 0~1）"""
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    i = int(round(q * (len(xs) - 1)))
    i = min(len(xs) - 1, max(0, i))
    return round(xs[i], 1)


def summarize(rows):
    if not rows:
        return {"count": 0, "verdict": "还没有数据，点「立即采样」开始记录。", "advice": []}

    col = lambda k: [r.get(k) for r in rows if r.get(k) is not None]
    ali, alimax = col("ali_avg"), col("ali_max")
    mm, mmmax = col("macmini_avg"), col("macmini_max")
    gw, gwmax = col("gateway_avg"), col("gateway_max")
    derp = col("derp_fastest_ms")
    reach = [r["derp_reachable"] for r in rows if r.get("derp_reachable")]

    direct = sum(1 for r in rows if r["path"] == "direct")
    relay = sum(1 for r in rows if str(r["path"]).startswith("relay"))
    offline = sum(1 for r in rows if r["path"] in ("offline", "unknown"))
    bad_reach = sum(1 for r in rows if (r.get("derp_reachable") or 0) < 20)
    spikes = sum(1 for x in alimax if x and x > 150)
    loss_rows = sum(1 for r in rows
                    if (r.get("ali_loss") or 0) > 0 or (r.get("macmini_loss") or 0) > 0)

    # 抖动指标：峰值 / 平均
    ratios = [(r["macmini_max"] / r["macmini_avg"]) for r in rows
              if r.get("macmini_max") and r.get("macmini_avg") and r["macmini_avg"] > 0]
    ratio_avg = round(sum(ratios) / len(ratios), 2) if ratios else None

    g, a, m, d = avg(gw), avg(ali), avg(mm), avg(derp)
    worst = max((r for r in rows if r.get("macmini_avg") is not None),
                key=lambda r: r["macmini_max"] or 0, default=None)

    conclusions, advice = [], []
    if g is not None:
        if g > 10:
            conclusions.append("内网这一段不稳（到路由器平均 %.1fms）" % g)
            advice.append("先解决内网：查 Wi-Fi 信号强度与信道，或改用网线；同时看路由器 CPU 与连接设备数。")
        else:
            conclusions.append("内网正常（到路由器平均 %.1fms）" % g)
    if a is not None:
        if a > 40:
            conclusions.append("国内基准偏高（阿里 DNS 平均 %.0fms）" % a)
            if g is not None and g <= 10:
                advice.append("出口拥塞：路由器里看实时上行占用与 QoS，确认是否有设备在上传。")
                advice.append("若长期偏高，属于宽带本身问题，考虑错峰、换套餐或换线路。")
        elif a > 20:
            conclusions.append("国内基准略高（阿里 DNS 平均 %.0fms）" % a)
        else:
            conclusions.append("国内基准正常（阿里 DNS 平均 %.0fms）" % a)
    if m is not None:
        conclusions.append("到 %s 平均 %.0fms" % (CFG["PEER_NAME"], m))
    if d is not None and d > 150:
        conclusions.append("最近中继 %.0fms（欧美方向）" % d)
        advice.append("备用中继都在欧美：可自建国内/香港 DERP，把兜底延迟压到 100ms 内。")
    if spikes > len(rows) * 0.3:
        conclusions.append("%d%% 的样本出现 >150ms 尖峰" % (100 * spikes // len(rows)))
        advice.append("尖峰频繁：对比同一时段其他设备的流量，必要时在路由器上限速。")
    if bad_reach:
        conclusions.append("%d 条样本可达中继少于 20" % bad_reach)
    if relay:
        conclusions.append("%d 条样本走的是中继" % relay)
        advice.append("打洞失败：在路由器上开启 UPnP / NAT-PMP 可提升直连成功率。")
    if not advice:
        advice.append("各项指标正常，保持观察即可。")

    return {
        "count": len(rows), "start": rows[0]["time"], "end": rows[-1]["time"],
        "ali_avg": a, "ali_peak": max(alimax) if alimax else None,
        "ali_p50": pct(ali, .5), "ali_p95": pct(ali, .95),
        "mm_avg": m, "mm_peak": max(mmmax) if mmmax else None,
        "mm_p50": pct(mm, .5), "mm_p95": pct(mm, .95), "mm_min": min(mm) if mm else None,
        "gw_avg": g, "gw_peak": max(gwmax) if gwmax else None, "gw_p50": pct(gw, .5),
        "derp_avg": d, "derp_region": rows[-1]["nearest_derp"], "derp_p50": pct(derp, .5),
        "path_now": rows[-1]["path"], "path_last_time": rows[-1]["time"],
        "direct": direct, "relay": relay, "offline": offline,
        "loss_rows": loss_rows, "bad_reach": bad_reach,
        "reach_min": min(reach) if reach else None, "reach_max": max(reach) if reach else None,
        "jitter_ratio": ratio_avg,
        "worst": ({"time": worst["time"], "value": worst.get("macmini_max"),
                   "avg": worst.get("macmini_avg")} if worst else None),
        "verdict": "；".join(conclusions) + "。",
        "advice": advice,
        "self_name": _self_name(), "peer_name": CFG["PEER_NAME"],
        "peer_host": CFG["PEER_HOST"], "ali_host": CFG["ALI_HOST"],
        "last_sample": _last_sample,
    }



# ---------------- 长 ping 诊断 ----------------
_LONG = {"job": None, "lock": threading.Lock()}


def _self_name():
    return CFG["SELF_NAME"] or os.uname().nodename


def _gateway_ip():
    for r in reversed(read_rows()):
        if r.get("gateway_ip"):
            return r["gateway_ip"]
    return CFG["GATEWAY_FALLBACK"] or "—"


def _ping_target(target, count, interval):
    cmd = ["/sbin/ping", "-c", str(count), "-i", str(interval), "-W", "2000", target]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=count * interval + 20).stdout
    except Exception as e:
        return {"status": "error", "error": str(e)[:80]}
    loss = re.search(r"([\d.]+)% packet loss", out)
    sent = re.search(r"(\d+) packets transmitted", out)
    rtt = re.search(r"round-trip min/avg/max/stddev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)", out)
    res = {
        "status": "done",
        "sent": int(sent.group(1)) if sent else count,
        "loss": float(loss.group(1)) if loss else (100.0 if not rtt else 0.0),
        "min": float(rtt.group(1)) if rtt else None,
        "avg": float(rtt.group(2)) if rtt else None,
        "max": float(rtt.group(3)) if rtt else None,
        "jitter": float(rtt.group(4)) if rtt else None,
    }
    return res


def _run_longping(job, count, interval):
    def one(t):
        t.update(_ping_target(t["host"], count, interval))
        with _LONG["lock"]:
            job["done_count"] = job.get("done_count", 0) + 1

    ths = [threading.Thread(target=one, args=(t,), daemon=True) for t in job["targets"]]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    job["status"] = "done"
    job["finished"] = datetime.now().strftime("%H:%M:%S")


def start_longping(count=40, interval=0.3):
    with _LONG["lock"]:
        cur = _LONG["job"]
        if cur and cur.get("status") == "running":
            return cur
        gw = _gateway_ip()
        job = {
            "id": datetime.now().strftime("%H%M%S"),
            "status": "running",
            "count": count,
            "interval": interval,
            "started": datetime.now().strftime("%H:%M:%S"),
            "finished": None,
            "done_count": 0,
            "targets": [
                {"name": "路由器网关", "kind": "gateway", "host": gw},
                {"name": "国内基准", "kind": "ali", "host": CFG["ALI_HOST"]},
                {"name": CFG["PEER_NAME"], "kind": "mini", "host": CFG["PEER_HOST"]},
            ],
        }
        _LONG["job"] = job
        threading.Thread(target=_run_longping, args=(job, count, interval), daemon=True).start()
        return job


# ---------------- DeepSeek 分析 ----------------
import deepseek

_AI = {"state": "idle", "at": None, "model": None, "text": "", "error": "", "usage": None,
       "messages": [], "digest": ""}


def _n(v, nd=1):
    return "—" if v is None else ("%.*f" % (nd, v))


def _ai_digest(rng="today"):
    rows = slice_range(read_rows(), rng)
    s = summarize(rows)
    lines = []
    lines.append("窗口：%s – %s，共 %d 条采样（60 秒一条）。" % (s.get("start") or "—", s.get("end") or "—", s.get("count") or 0))
    lines.append("设备：本机 %s；对端 %s（%s）；国内基准 %s。"
                 % (_self_name(), CFG["PEER_NAME"], CFG["PEER_HOST"] or "未配置", CFG["ALI_HOST"]))
    lines.append("局域网：当前网关 %s。" % _gateway_ip())
    lines.append("路径：%d 次直连 / %d 次中继；当前路径 %s（最近判定时间 %s）。"
                 % (s.get("direct") or 0, s.get("relay") or 0, s.get("path_now") or "未知", s.get("path_last_time") or "—"))
    lines.append("")
    lines.append("关键指标（P50 / 平均 / P95 / 峰值）：")
    lines.append("- 路由器网关：" + " / ".join([_n(s.get("gw_p50")), _n(s.get("gw_avg")), _n(s.get("gw_p95")), _n(s.get("gw_peak"))]))
    lines.append("- 国内基准 %s：" % CFG["ALI_HOST"]  + " / ".join([_n(s.get("ali_p50")), _n(s.get("ali_avg")), _n(s.get("ali_p95")), _n(s.get("ali_peak"))]))
    lines.append("- %s（Tailscale 直连）：" % CFG["PEER_NAME"]  + " / ".join([_n(s.get("mm_p50")), _n(s.get("mm_avg")), _n(s.get("mm_p95")), _n(s.get("mm_peak"))]))
    lines.append("- 备用中继最快延迟：" + _n(s.get("derp_p50")) + "（区域 " + str(s.get("derp_region") or "未知") + "）")
    lines.append("")
    lines.append("抖动与丢包：峰值/平均抖动比 %s；出现丢包的采样 %s 条；中继可达数 %s–%s（低于 20 视为国际出口有丢包）；"
                 "最差样本 %s。"
                 % (_n(s.get("jitter_ratio"), 2), s.get("loss_rows"), s.get("reach_min"), s.get("reach_max"),
                    (str((s.get("worst") or {}).get("time")) + " " + _n((s.get("worst") or {}).get("value")) + "ms")
                    if s.get("worst") else "—"))
    lines.append("")
    tails = rows[-10:]
    lines.append("最近 %d 条明细（时间 | 网关 平均/峰值 | 阿里 平均/峰值 | mini 平均/峰值 | 中继最快 | 路径）：" % len(tails))
    for r in tails:
        lines.append("%s | %s/%s | %s/%s | %s/%s | %s | %s" % (
            str(r.get("time", ""))[-8:], _n(r.get("gateway_avg")), _n(r.get("gateway_max")),
            _n(r.get("ali_avg")), _n(r.get("ali_max")), _n(r.get("macmini_avg")), _n(r.get("macmini_max")),
            _n(r.get("derp_fastest_ms")), r.get("path") or "—"))
    job = _LONG.get("job")
    if job and job.get("status") == "done":
        lines.append("")
        lines.append("长 ping 诊断（每次 %d 包，%s 完成）：" % (job.get("count"), job.get("finished")))
        for t in job["targets"]:
            if t.get("status") == "done":
                lines.append("- %s %s：平均 %s / 最小 %s / 最大 %s / 抖动 %s / 丢包 %s%%" % (
                    t["name"], t["host"], _n(t.get("avg")), _n(t.get("min")), _n(t.get("max")),
                    _n(t.get("jitter")), _n(t.get("loss"))))
    lines.append("")
    lines.append("判读口径（本面板采用）：网关平均 >10ms 判内网不稳（<5ms 正常）；阿里平均 >40ms 判出口偏高；"
                 "中继延迟 >150ms 判偏远；超过 30% 的样本阿里峰值 >150ms 判尖峰频繁；路径出现中继＝打洞失败。")
    return "\n".join(lines)


def _ai_messages():
    return [{"role": "system", "content": deepseek.SYSTEM_PROMPT +
             "\n\n【当前监测数据】\n" + (_AI.get("digest") or _ai_digest("today"))}] + \
           [{"role": m["role"], "content": m["text"]} for m in _AI["messages"][-20:]]


def _ai_finish(res):
    if res.get("ok"):
        _AI["messages"].append({"role": "assistant", "text": res["text"], "at": res.get("at")})
        _AI.update(state="done", text=res["text"], at=res.get("at"), model=res.get("model"),
                   usage=res.get("usage"), error="")
    else:
        _AI.update(state="error", error=res.get("error", "分析失败"), model=res.get("model"))
    deepseek.save_chat({"messages": _AI["messages"], "model": _AI.get("model"),
                        "digest": _AI.get("digest"), "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    return dict(_AI)


def start_ai(model=None):
    if _AI["state"] == "running":
        return dict(_AI)
    _AI.update(state="running", error="", messages=[], text="", at=None,
               digest=_ai_digest("today"))
    _AI["messages"].append({"role": "user", "text": deepseek.USER_TEMPLATE.format(data=_AI["digest"])[:9000],
                            "at": datetime.now().strftime("%H:%M:%S")})

    def run():
        try:
            _ai_finish(deepseek.chat(_ai_messages(), model))
        except Exception as e:
            _AI.update(state="error", error="分析失败：%s" % e)

    threading.Thread(target=run, daemon=True).start()
    return dict(_AI)


def start_chat(text, model=None):
    if _AI["state"] == "running":
        return {"ok": False, "error": "上一条还在生成，稍等两秒"}
    if not _AI["messages"]:
        return {"ok": False, "error": "先点一次「开始分析」，再接着问"}
    if not _AI.get("digest"):
        _AI["digest"] = _ai_digest("today")
    _AI["messages"].append({"role": "user", "text": text[:2000],
                            "at": datetime.now().strftime("%H:%M:%S")})
    _AI.update(state="running", error="")

    def run():
        try:
            _ai_finish(deepseek.chat(_ai_messages(), model))
        except Exception as e:
            _AI.update(state="error", error="请求失败：%s" % e)

    threading.Thread(target=run, daemon=True).start()
    return dict(_AI)


def _ai_stream_begin(text, model, reset):
    """准备一轮对话，返回 (messages, err)。用户消息先进历史。"""
    if _AI["state"] == "running":
        return None, "上一条还在生成，稍等"
    if not _AI["messages"]:
        disk = deepseek.load_chat()
        if disk and disk.get("messages"):
            _AI["messages"] = disk["messages"]
            _AI["digest"] = disk.get("digest") or ""
    _AI.update(state="running", error="", usage=None)
    if reset:
        _AI.update(messages=[], text="", at=None, digest=_ai_digest("today"))
        user = deepseek.USER_TEMPLATE.format(data=_AI["digest"])[:9000]
    else:
        if not _AI["messages"]:
            _AI.update(state="idle")
            return None, "先点一次「开始分析」，再接着问"
        user = (text or "")[:2000]
        if not user:
            _AI.update(state="idle")
            return None, "消息为空"
    if not _AI.get("digest"):
        _AI["digest"] = _ai_digest("today")
    _AI["messages"].append({"role": "user", "text": user,
                            "at": datetime.now().strftime("%H:%M:%S")})
    return _ai_messages(), None


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=APP_DIR, **kw)

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path, _, qs = self.path.partition("?")
        if path == "/api/samples":
            rng = "today"
            for kv in qs.split("&"):
                if kv.startswith("range="):
                    rng = kv[6:]
            rows = slice_range(read_rows(), rng)
            self._json({"rows": rows})
            return
        if path == "/api/summary":
            rng = "today"
            for kv in qs.split("&"):
                if kv.startswith("range="):
                    rng = kv[6:]
            self._json(summarize(slice_range(read_rows(), rng)))
            return
        if path == "/api/health":
            self._json({"ok": True, "log": LOG})
            return
        if path == "/api/longping":
            self._json(_LONG["job"] or {"status": "idle"})
            return
        if path == "/api/ai/status":
            st = deepseek.status()
            if not _AI["messages"] and _AI["state"] == "idle":
                disk = deepseek.load_chat()
                if disk and disk.get("messages"):
                    _AI["messages"] = disk["messages"]
                    _AI["digest"] = disk.get("digest") or ""
                    _AI["state"] = "done"
            st["job"] = _AI
            self._json(st)
            return
        if path == "/api/ai/last":
            self._json(deepseek.load_last() or {})
            return
        if path == "/":
            self.path = "/index.html"
        super().do_GET()

    def _sse_write(self, text):
        try:
            self.wfile.write(text.encode("utf-8"))
            self.wfile.flush()
        except Exception:
            pass

    def _sse(self, msgs, model):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        acc = []
        try:
            for delta in deepseek.stream_chat(msgs, model):
                acc.append(delta)
                self._sse_write("data: " + json.dumps({"type": "delta", "text": delta},
                                                      ensure_ascii=False) + "\n\n")
        except Exception as e:
            _AI.update(state="error", error="%s" % e)
            self._sse_write("data: " + json.dumps({"type": "error", "error": "%s" % e},
                                                  ensure_ascii=False) + "\n\n")
            self._sse_write("data: [DONE]\n\n")
            return
        full = "".join(acc)
        if full:
            _AI["messages"].append({"role": "assistant", "text": full,
                                    "at": datetime.now().strftime("%H:%M:%S")})
            _AI.update(state="done", text=full, at=_AI["messages"][-1]["at"],
                       model=deepseek.get_model(), error="")
        else:
            _AI.update(state="error", error="模型没有返回内容")
        deepseek.save_chat({"messages": _AI["messages"], "model": deepseek.get_model(),
                            "digest": _AI.get("digest"),
                            "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        self._sse_write("data: " + json.dumps({"type": "done"}, ensure_ascii=False) + "\n\n")
        self._sse_write("data: [DONE]\n\n")

    def do_POST(self):
        if self.path == "/api/ai/config":
            try:
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}"
                d = json.loads(body.decode("utf-8"))
                if d.get("key"):
                    deepseek.set_key(d["key"])
                if d.get("model"):
                    deepseek.set_model(d["model"])
                self._json({"ok": True, "has_key": bool(deepseek.get_key()), "model": deepseek.get_model()})
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 400)
            return
        if self.path in ("/api/ai/analyze/stream", "/api/ai/chat/stream"):
            try:
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}"
                d = json.loads(body.decode("utf-8"))
            except Exception:
                d = {}
            reset = self.path.endswith("analyze/stream")
            msgs, err = _ai_stream_begin(d.get("message"), d.get("model"), reset)
            if err:
                self._json({"ok": False, "error": err}, 409)
                return
            self._sse(msgs, d.get("model"))
            return
        if self.path == "/api/ai/chat":
            try:
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}"
                d = json.loads(body.decode("utf-8"))
            except Exception:
                d = {}
            txt = (d.get("message") or "").strip()
            if not txt:
                self._json({"ok": False, "error": "消息为空"}, 400)
                return
            self._json(start_chat(txt, d.get("model")))
            return
        if self.path == "/api/ai/analyze":
            model = None
            try:
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}"
                model = json.loads(body.decode("utf-8")).get("model")
            except Exception:
                pass
            self._json(start_ai(model))
            return
        if self.path == "/api/longping":
            n = 40
            try:
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}"
                n = int(json.loads(body.decode("utf-8")).get("count", 40))
            except Exception:
                pass
            self._json(start_longping(count=max(10, min(200, n))))
            return
        if self.path != "/api/sample":
            self._json({"error": "not found"}, 404)
            return
        if not _sample_lock.acquire(blocking=False):
            self._json({"ok": False, "msg": "上一次采样还没结束"}, 429)
            return
        try:
            proc = subprocess.run(["/bin/bash", WATCH, "--once"], capture_output=True, text=True, timeout=90)
            ok = proc.returncode == 0
            _last_sample.update(at=datetime.now().strftime("%H:%M:%S"), ok=ok,
                                msg=(proc.stdout or proc.stderr).strip()[-200:])
            self._json({"ok": ok, "msg": _last_sample["msg"]})
        finally:
            _sample_lock.release()


def main():
    if len(sys.argv) > 1:
        start = int(sys.argv[1])
    else:
        start = int(os.environ.get("PANEL_PORT", "8767"))
    # 端口被占用时直接报错退出，不自动漂到下一个空闲端口：
    # 漂移会让浏览器停在旧地址读到旧实例的数据（踩过的坑）。
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", start), Handler)
    except OSError as e:
        sys.exit("端口 %d 已被占用（%s）。\n"
                 "  换端口：python3 app/server.py 9000\n"
                 "  或先停掉旧实例：bash scripts/stop-panel.command"
                 % (start, e))
    port = start
    with open(os.path.join(ROOT_DIR, ".panel-port"), "w") as fh:
        fh.write(str(port))
    print("面板已启动: http://127.0.0.1:%d   (Ctrl-C 停止)" % port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
