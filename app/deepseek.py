#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepSeek 分析封装（纯标准库）

密钥只存本地文件 app/.deepseek_key（权限 600），不接受通过接口读回原值。
"""
import json
import os
import stat
import urllib.error
import urllib.request
from datetime import datetime

APP_DIR = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(APP_DIR, ".deepseek_key")
MODEL_FILE = os.path.join(APP_DIR, ".deepseek_model")
OUT_FILE = os.path.join(os.path.dirname(APP_DIR), "ai-analysis.md")
CHAT_FILE = os.path.join(os.path.dirname(APP_DIR), "ai-chat.json")
ENDPOINT = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"

_last = {"at": None, "model": None, "text": "", "error": "", "usage": None}


def get_key():
    env = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if env:
        return env
    try:
        with open(KEY_FILE, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def set_key(key):
    key = (key or "").strip()
    if not key.startswith("sk-"):
        raise ValueError("密钥格式不对，DeepSeek 的 key 以 sk- 开头")
    os.makedirs(APP_DIR, exist_ok=True)
    with open(KEY_FILE, "w", encoding="utf-8") as fh:
        fh.write(key)
    os.chmod(KEY_FILE, stat.S_IRUSR | stat.S_IWUSR)
    return True


def get_model():
    try:
        with open(MODEL_FILE, encoding="utf-8") as fh:
            m = fh.read().strip()
    except OSError:
        m = ""
    return m or DEFAULT_MODEL


def set_model(model):
    model = (model or DEFAULT_MODEL).strip()
    if model not in ("deepseek-chat", "deepseek-reasoner"):
        model = DEFAULT_MODEL
    with open(MODEL_FILE, "w", encoding="utf-8") as fh:
        fh.write(model)
    return model


def status():
    last = _last
    if not last.get("text"):
        disk = load_last()
        if disk:
            last = disk
    return {
        "has_key": bool(get_key()),
        "model": get_model(),
        "last": last,
    }


SYSTEM_PROMPT = """你是一名家庭网络运维工程师，服务对象是普通用户（macOS + 家用宽带）。规则：
1. 只依据给定数据下结论，每个判断都要带具体数字；证据不足就说"不足以判断"，不要编。
2. 结论必须能落地：任何建议都要写清"在哪儿改什么"——macOS 写系统设置的具体层级、路由器写后台菜单名、命令写成可以直接粘贴的形式。
3. 禁止"检查一下""优化设置""换个信道试试"这种没有落点的空话。
4. 命令只给只读诊断类（ping / nettop / scutil --dns / route -n get default / tailscale status / system_profiler SPAirPortDataType 等）。
   凡是会改网络配置、改系统代理、需要 sudo 或会断网的操作，必须单独标注"（会短暂断网，你自己确认后再执行）"，不要混在诊断命令里。
4. 建议按"先免费低成本、后花钱换设备"排序，并给出耗时和成本量级。
5. 全部用简体中文，不客套，不吹捧。"""

USER_TEMPLATE = """以下是本机最近一次网络链路监测数据（Tailscale 直连/中继 + 家庭宽带出口 + 局域网网关）。

{data}

输出格式严格照下面来，简体中文，总长 800 字以内：

【结论】一句话讲清现在什么状态、主要问题在哪一环。

【依据】3–5 行，每行必须以具体数字开头或包含数字，例如"网关 P50 4.6ms 但峰值 195ms，抖动 42 倍"。

【执行方案】按优先级编号 1/2/3，每条固定四行，缺一行就算失败：
1. 方案名（动词开头，括号里写"耗时约 X 分钟 / 成本"）
   - 操作：具体到点哪里。macOS 写"系统设置 → 网络 → Wi-Fi → 详细信息 → …"这样的完整路径；路由器写后台菜单名（不确定型号就写"路由器后台 → 无线设置 → 信道"这类通用路径并说明怎么看型号）
   - 验证：跑什么命令或看面板哪个数字
   - 通过标准：给具体数值门槛，例如"网关 P50 ≤5ms 且峰值 ≤20ms，连续 25 次采样成立"

【只读命令】给一段 ```sh 代码块，整段可以直接粘贴到终端执行，只包含只读诊断命令，用来复现和确认问题。命令里不要出现 sudo、不要修改任何配置。

【数据缺口】判断还差什么数据；不缺就写"无"。"""

def chat(messages, model=None):
    """通用对话调用，messages 为完整的 OpenAI 格式消息列表。"""
    key = get_key()
    if not key:
        return {"ok": False, "error": "还没有配置 DeepSeek 密钥，请先在面板里粘贴一次", "error_kind": "no_key"}
    model = set_model(model or get_model())
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 1200,
        "stream": False,
    }
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        method="POST",
    )
    at = datetime.now().strftime("%H:%M:%S")
    try:
        with urllib.request.urlopen(req, timeout=75) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:300]
        msg = {401: "密钥无效或已过期（401）", 402: "账户余额不足（402）",
               429: "请求太频繁，等一会儿再试（429）"}.get(e.code, "接口返回 %d" % e.code)
        return {"ok": False, "error": msg, "detail": detail, "error_kind": "http", "at": at, "model": model}
    except Exception as e:
        return {"ok": False, "error": "请求失败：%s" % e, "error_kind": "network", "at": at, "model": model}

    text = (body.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
    if not text:
        return {"ok": False, "error": "模型没有返回内容", "error_kind": "empty", "at": at, "model": model}
    return {"ok": True, "text": text, "at": at, "model": model, "usage": body.get("usage")}


def stream_chat(messages, model=None):
    """逐段 yield 文本片段（DeepSeek SSE）。出错抛 RuntimeError。"""
    key = get_key()
    if not key:
        raise RuntimeError("还没有配置 DeepSeek 密钥")
    model = set_model(model or get_model())
    payload = {"model": model, "messages": messages, "temperature": 0.3,
               "max_tokens": 1200, "stream": True}
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=75)
    except urllib.error.HTTPError as e:
        msg = {401: "密钥无效或已过期（401）", 402: "账户余额不足（402）",
               429: "请求太频繁，等一会儿再试（429）"}.get(e.code, "接口返回 %d" % e.code)
        raise RuntimeError(msg)
    except Exception as e:
        raise RuntimeError("请求失败：%s" % e)
    with resp:
        for raw in resp:
            line = raw.decode("utf-8", "ignore").strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                j = json.loads(data)
            except ValueError:
                continue
            ch = (j.get("choices") or [{}])[0]
            delta = (ch.get("delta") or {}).get("content")
            if delta:
                yield delta


def analyze(data_text, model=None):
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_TEMPLATE.format(data=data_text)},
    ]
    res = chat(msgs, model)
    if res.get("ok"):
        _last.update(at=res["at"], model=res["model"], text=res["text"], error="", usage=res.get("usage"))
        try:
            with open(OUT_FILE, "w", encoding="utf-8") as fh:
                fh.write("# DeepSeek 分析 %s\n\n%s\n"
                         % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), res["text"]))
        except OSError:
            pass
    else:
        _last.update(at=res.get("at"), model=res.get("model"), error=res.get("error", ""), text="")
    return res


def save_chat(payload):
    try:
        with open(CHAT_FILE, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
    except OSError:
        pass


def load_chat():
    try:
        with open(CHAT_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def load_last():
    try:
        with open(OUT_FILE, encoding="utf-8") as fh:
            content = fh.read()
    except OSError:
        return None
    first = content.split("\n", 1)[0].replace("# DeepSeek 分析 ", "")
    return {"at": first, "text": content.split("\n\n", 1)[-1].strip(), "model": get_model()}
