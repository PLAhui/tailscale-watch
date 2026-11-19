# Tailscale 链路监控面板 · 开发提示词

> 用途：把下面全文喂给任意一个具备文件读写与命令执行能力的 AI，即可从零复刻或继续迭代这套系统。
> 写法是"提示词"而非"文档"：包含角色设定、硬约束、验收标准，以及必须避开的坑。

---

## 一、你的角色与任务

你是一名 macOS 网络诊断工程师 + 前端工程师。任务是在用户本机搭建一套**本地 Web 监控面板**，把 `tailscale status` / `ping` / `tailscale netcheck` 的零散输出，变成"一眼看出问题在哪一环"的可视化界面，并接入大模型做诊断建议。

目标用户是普通家庭用户（不是网工）：他关心的是"我这网到底哪儿慢、该改什么"，不是原始指标。**一切设计围绕"减少信息、突出结论"**。

---

## 二、硬约束（违反即返工）

1. **零第三方依赖**：后端只用 Python 3 标准库（`http.server` / `subprocess` / `urllib.request` / `threading` / `csv`）；前端单文件 `index.html`，ECharts 必须**下载到本地** `app/vendor/echarts.min.js` 离线引用，禁止 CDN（用户可能正处在断网/高延迟环境）。
2. **不能用需要 root 的方式采集**：`tailscale` CLI 在这台机器上必须走 `sudo -n`（或先试免 sudo，遇到 stdout 里的 `Error:` 再回退）。`ping -i 0.3` 在 macOS **实测免 root 可用**，不需要 sudo。
3. **采样与展示解耦**：采集由 launchd 每 60 秒跑一次写 CSV；面板只读 CSV。面板挂了不影响采集，采集挂了面板显示旧数据。
4. **端口固定 8767**，写进 `.panel-port` 与 plist，禁止让它自动漂到别的空闲端口（历史事故：旧实例占着旧端口，浏览器看到的是旧代码）。
5. **采集频率 60 秒**，不做更快轮询（会污染延迟数据）。
6. **所有界面文案用简体中文**，保留必要网络术语（P50 / 直连 / 中继 / 抖动）。
7. 涉及数字的结论必须给出**阈值口径**，且阈值要在页面上可查（图例或脚注）。

---

## 三、数据层规范

### 3.1 CSV schema（16 列，顺序不可变，前端按列名取值）

```
time, nearest_derp, derp_fastest, derp_fastest_ms, derp_reachable,
ali_loss, ali_avg, ali_max, macmini_loss, macmini_avg, macmini_max,
path, gateway_loss, gateway_avg, gateway_max, gateway_ip
```

- `time`：`YYYY-MM-DD HH:MM:SS`（本地时间）
- `nearest_derp` / `derp_fastest`：DERP 区域英文名 + 三字码（如 `Los Angeles` / `lax`）
- `path`：`direct` 或 `relay_<region>`，前端显示"直连/中继"
- `gateway_ip`：用 `route -n get default` 动态取，不要写死
- 空值写 `—` 或留空，前端按"缺失"处理，**不要填 0**

### 3.2 探测目标（固定三个 + DERP）

| 目标 | 地址 | 用途 |
|---|---|---|
| 路由器网关 | 动态取默认路由 | 内网 / Wi-Fi 底色 |
| 阿里 DNS | `223.5.5.5` | 国内出口基准 |
| 对端主机 | Tailscale 100.x 地址 | 真实直连体验 |
| DERP 备用 | `tailscale netcheck` 结果 | 兜底通道质量 |

`ping` 命令形态：`/sbin/ping -c N -i 0.3 -W 2000 <host>`。
解析要点：`X% packet loss`、`N packets transmitted`、`round-trip min/avg/max/stddev = a/b/c/d`；同时兼容 `rtt` 写法。

### 3.3 判读阈值（前端配色与结论共用一套）

| 指标 | 正常 | 偏高 | 超标 |
|---|---|---|---|
| 网关平均 | < 5ms | 5–10ms | > 10ms（判内网不稳） |
| 阿里 DNS 平均 | < 40ms | 40–100ms | > 100ms |
| 对端平均 | < 100ms | 100–200ms | > 200ms |
| 中继最快 | < 200ms | 200–400ms | > 400ms |
| 中继可达数 | ≥ 20 | 15–19 | < 20 视为国际出口丢包 |
| 抖动（峰值/平均） | < 2 倍 | 2–3 倍 | > 3 倍 |

配色：正常 `#0d9488`（青绿）、偏高 `#d9820b`（琥珀）、超标 `#dc2626`（红）；主色 `#2f6fed`（蓝，仅用于强调与交互）。

---

## 四、后端接口

全部 `GET/POST` JSON，`Cache-Control: no-store`。

```
GET  /                    面板页面
GET  /api/samples         原始采样      ?range=today|6h|all
GET  /api/summary         聚合统计与结论（前端只依赖这一个）
GET  /api/health          存活检查
POST /api/sample          立即采样一次
GET  /api/longping        最近一次长 ping 任务
POST /api/longping        启动长 ping    {"count": 40}
GET  /api/ai/status       密钥状态 + 会话消息
GET  /api/ai/last         上次分析（磁盘缓存）
POST /api/ai/config       存密钥/模型   {"key":"sk-...","model":"deepseek-chat"}
POST /api/ai/analyze       一次性分析（兼容用）
POST /api/ai/analyze/stream  流式分析  ← 前端实际使用
POST /api/ai/chat          一次性追问（兼容用）
POST /api/ai/chat/stream     流式追问  ← 前端实际使用
```

`/api/summary` 必须返回（字段名固定，前端直接取）：

```
count, start, end, ali_avg, ali_peak, ali_p50, ali_p95,
mm_avg, mm_peak, mm_p50, mm_p95, mm_min,
gw_avg, gw_peak, gw_p50, gw_p95,
derp_avg, derp_region, derp_p50,
path_now, path_last_time, direct, relay, offline,
loss_rows, bad_reach, reach_min, reach_max, jitter_ratio,
worst{time,value,avg}, verdict, advice[], last_sample
```

要点：
- `pct()` 用最近秩法算分位；P50 缺失时前端回退到平均值。
- `verdict` 是**一句话人话结论**，多句用中文分号 `；` 分隔（前端会按分号切成"标题 + 若干标签"）。
- `advice` 是按优先级排的短句数组，每条 20–40 字，**不要写成长段落**。
- 长 ping：三个目标**并行**跑（`threading.Thread`），40 包 × 0.3s ≈ 12 秒出全部结果。

---

## 五、前端信息架构（关键：只有四块）

页面从上到下**只能有四个区块**，越靠上越重要：

1. **结论条**（hero）
   - 左：`结论` 小标签 + 一句大字结论（21px，从 `verdict` 第一句取）+ 其余分句做成灰色圆角小标签
   - 左下方：最多 3 条建议，多余折叠为「展开余下 N 条」
   - 右：**三个大数字**（27px）——对端 P50（真实体验）/ 出口 P50（网速基准）/ 网关 P50（内网底色），每个下面一行小字 `均 · P95 · 峰`
   - 右下角一行小字：路径、备用中继、丢包、可达、样本数
   - 左侧 3px 竖条：有异常时变琥珀/红，正常为蓝
2. **链路拓扑**：SVG，本机 → 路由器 → 互联网 → 对端 四个节点，实线为主路径、虚线为备用中继弧线；每段标"最近一次采样值 +（范围 P50）"；按阈值变色
3. **延迟趋势**：单图。**默认只显示两条线**（对端 + 网关），出口与中继在图例里灰着，点一下才出现；图上画 5/40/200ms 三条虚线阈值；下方一条色带显示逐次直连/中继
4. **两个折叠区**（默认收起）：`更多图表`（对端分布 + 抖动强度）、`全部明细`（逐条采样表，异常行标黄/红）

### 视觉规范
- 卡片数 ≤ 4，不要堆 KPI 卡；同一信息只出现一次
- 字号三级：结论 21px / 区块标题 13.5px / 辅助 11–11.5px
- 主要靠留白和虚线分隔，彩色只用于阈值状态
- 必须支持深色模式（`prefers-color-scheme`）与 ≤1120px 的单列响应式

### 动效（重要坑）
- **不要用 SVG SMIL（`animateMotion`）**：目标渲染环境会忽略它，用户看到的是"完全静止"。
- 统一用 JS `requestAnimationFrame` 逐帧改 `cx/cy` / `r` / `left`。光点速度与延迟成反比，约 1–3 秒走完一段；节点脉冲环每 3.2 秒一次；趋势图扫描带 9 秒从左到右。
- 动效必须**基于真实数据**，数字要跟着最近一次采样更新，不能是纯装饰。

---

## 六、AI 分析模块（DeepSeek）

### 6.1 密钥与安全
- 密钥只写本机 `app/.deepseek_key`，`chmod 600`；页面里粘贴一次即可，**不允许经聊天/日志传递**
- 接口不回显密钥原文，只回 `has_key: bool`
- 允许切 `deepseek-chat`（默认）/ `deepseek-reasoner`

### 6.2 上下文（每次请求都带，防止模型瞎猜）
系统提示 + 一段结构化数据摘要：

```
窗口 / 设备（含本机 Tailscale IP、公网出口 IP 与运营商、NAT 类型）
局域网（真实网关 IP、主机名、CPE 类型）
路径（直连/中继次数、当前路径）
关键指标（网关/出口/对端/中继 的 P50 / 平均 / P95 / 峰值）
抖动与丢包（抖动比、丢包条数、中继可达区间、最差样本）
最近 10 条明细（时间 | 各目标 平均/峰值 | 中继 | 路径）
长 ping 结果（若跑过）
判读口径（把 3.3 的阈值表原文附上，让模型和面板用同一把尺子）
```

### 6.3 输出格式（强制四段 + 可执行）
```
【结论】一句话，说清状态与问题在哪一环
【依据】3–5 行，每行必须含具体数字
【执行方案】编号 1/2/3，每条固定四行：
   方案名（动词开头，括号写"耗时约 X 分钟 / 成本"）
   - 操作：具体到点哪里（macOS 写系统设置完整层级；路由器写后台菜单名；有真实 IP 就用真实 IP）
   - 验证：跑什么命令、看哪个数字
   - 通过标准：给数值门槛（如"网关 P50 ≤5ms 且峰值 ≤20ms，连续 25 次成立"）
【只读命令】一段 ```sh 代码块，整段可直接粘贴；禁止 sudo、禁止改配置
【数据缺口】还缺什么数据；不缺写"无"
```

提示词里必须明确禁止："检查一下""优化设置""换个信道试试"这类没有落点的空话。

### 6.4 交互
- 首轮出诊断后，**支持继续追问**（多轮），追问走同一份数据上下文
- **必须流式**（SSE）：后端把 DeepSeek 的 `stream:true` 原样转为 `text/event-stream`，事件格式 `{"type":"delta","text":"…"}` / `{"type":"done"}` / `{"type":"error"}`；前端用 `ReadableStream` 按 `\n\n` 切分渲染，末尾带闪烁光标
- 多轮历史只送最近 20 条；对话持久化到 `ai-chat.json`，重启后仍在
- 会话里首条"数据快照"在 UI 上压缩成一行（`监测数据快照 · 时间段 · N 条采样`），不要把整段 digest 铺在聊天里
- 回答里的代码块右上角要有「复制」按钮

---

## 七、部署与运维

```
launchd/agent.plist.template           StartInterval 60 → netcheck-watch.sh --once
launchd/panel.plist.template           RunAtLoad + KeepAlive → python3 app/server.py 8767

# 安装后落在：
~/Library/LaunchAgents/io.github.tailscale-watch.agent.plist
~/Library/LaunchAgents/io.github.tailscale-watch.panel.plist
```

模板里的 `__PROJECT_DIR__` 由 `scripts/install-launchd.sh` 用 sed 替换成仓库绝对路径，因此仓库可以放在任意位置。

- 面板进程挂了由 launchd 拉起；改代码后 `pkill -f app/server.py` 让 KeepAlive 重启即可
- 临时启停用 `scripts/start-panel.command` / `scripts/stop-panel.command`；常驻用 `make install`
- 目录约定：

```
tailscale-watch/
  netcheck-watch.sh          采样（--once / --daemon / --bg / --stop）
  netcheck-report.py         命令行中文报表（--today / --tail N）
  netcheck-report.sh         报表 shell 包装
  app/server.py              面板后端
  app/index.html             前端单文件
  app/deepseek.py            大模型封装（密钥、流式、缓存）
  app/vendor/echarts.min.js  离线图表库
  scripts/                   启停与 launchd 安装脚本
  launchd/                   plist 模板（含 __PROJECT_DIR__ 占位）
  docs/DEVELOPMENT.md        本文件
  docs/screenshots/          README 用截图
  Makefile / README.md / LICENSE / CHANGELOG.md / CONTRIBUTING.md

  # 运行时产物（均已 gitignore）
  netcheck-log.csv           16 列实时数据
  ai-chat.json / ai-analysis.md  对话与分析缓存
  .panel-port / var/         端口标记与日志
  app/.deepseek_key          密钥（chmod 600）
```

---

## 八、必须避开的坑（都是真实踩过的）

1. **端口漂移**：曾因旧实例占 8767、新代码落到 8766，浏览器一直拿旧数据，表现为"P50 全部显示 —"。修法：端口写死，启动前确认只有一个实例（`lsof -nP -iTCP -sTCP:LISTEN | grep 876`）。
2. **`curl -s` 判活不可靠**：404 也算成功，必须 `curl -sf`。
3. **ECharts 局部 setOption 会炸**：`setOption(option, {notMerge:true})` 只给部分轴会报 `Cannot read properties of undefined (reading 'get')` 并白屏。每次返回**完整配置**并用 `setOption(opt, true)`。
4. **DOM 里删块要同步删 JS 引用**：改为折叠布局时删掉了 `#heroMain`，JS 仍在 `$('heroMain').style...`，于是整页报 `Cannot read properties of null` 并中断渲染。改结构后必须做一次"JS 引用的 id 是否都存在"的比对。
5. **图表容器初始尺寸为 0**：折叠区里的图表 init 后宽度是 0。要在 `details` 的 `toggle` 事件里 `resize()`，并在首屏渲染后补两次延迟 resize（60ms / 400ms）。
6. **`screencapture` 无权限**：截图验证一律用 Chrome headless：
   `--headless=new --disable-gpu --hide-scrollbars --virtual-time-budget=<ms> --window-size=W,H --screenshot=x.png <url>`
   且 **`--virtual-time-budget` 必加**，否则异步 fetch 未返回，截到的是骨架屏。
7. **headless 下动画不推进**：CSS/SMIL 在无头模式可能两帧相同，不能靠截图验证动效；改看 `--dump-dom` 里 `cx/cy` / `left` 是否变化，或直接用真实浏览器确认。
8. **`tailscale` 的错误走 stdout 且退出码为 0**：必须按输出内容判定，不能只看 returncode。
9. **改 HTML 后先做语法校验**：正则取出 `<script>` 末段写 `/tmp/p.js`，`node --check`。历史上因为残留函数导致 `Identifier 'chart' has already been declared`，整页卡在"正在加载"。
10. **不要在页面加载时同步跑长任务**：长 ping、AI 请求一律后台线程 + 前端轮询/流式，避免请求超时把面板拖死。

---

## 九、验收清单（交付前逐条自检）

**数据**
- [ ] CSV 16 列顺序正确，新增行不破坏历史文件（旧数据另存 `netcheck-log-旧版.csv`）
- [ ] 重启面板后数字与 CSV 最后一行一致，无 `—` 空洞
- [ ] 网关 IP 来自默认路由而非写死

**界面**
- [ ] 首屏只看四个区块；六张以下卡片；三个主数字清晰可读
- [ ] 结论是"一句话 + 标签"，不是一整段
- [ ] 图表默认只两条线；折叠区能展开且展开后图表宽度正常
- [ ] 深色模式、窄屏（≤1120px）均不塌
- [ ] 拓扑光点确实在动（在真实浏览器里目视确认）

**功能**
- [ ] 「立即采样」后 60 秒内新数据出现在表尾
- [ ] 「长 ping」三个目标并行，40 包约 12 秒返回 min/avg/max/stddev
- [ ] 「AI 分析」首字 ≤1.5 秒出现，逐段吐出，代码块有复制按钮
- [ ] 追问能承接上文；关掉面板重开，对话还在
- [ ] 密钥缺失/错误时给出明确提示，不静默失败

**健壮性**
- [ ] 面板进程被杀后 launchd 自动拉起，仍监听 8767
- [ ] 网络断开时页面显示旧数据 + 明确时间戳，而不是空白

---

## 十、可选扩展（按需，不必一次做完）

- 结论判定从"均值 vs 阈值"改为"P50 + P95 vs 阈值"，消除单尖峰导致的结论翻转
- 路径掉到中继时发 macOS 系统通知
- 历史数据按天切文件，保留 30 天
- 拓扑上标注"过去 1 小时最差时刻"
- 若用户提供 Tailscale API Token（`tskey-api-…`），自动发现全设备清单并按设备测延迟（**注意**：只能测"从本机看出去"的单视角，云端 API 不提供设备间延迟矩阵）
