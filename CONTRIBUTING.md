# 贡献指南

感谢你愿意改进这个项目。它是一个**刻意保持轻量**的本地工具：没有构建步骤、没有依赖管理、没有 CI 之外的脚手架。请遵守下面的约定，能省掉大部分 review 往返。

## 环境

- macOS 12+（采样依赖 `/sbin/ping` 与 `tailscale` CLI）
- Python 3.9+（只用标准库，**不接受**引入第三方 Python 包）
- Node.js 仅用于校验前端 JS 语法（`node --check`），不是运行时依赖

## 本地开发

```bash
git clone <repo> && cd tailscale-watch
bash scripts/start-panel.command      # 起面板，默认 8767
python3 netcheck-watch.sh --once      # 手动采一条样本
python3 netcheck-report.py --tail 10  # 命令行看最近 10 条
```

## 提交前自检

```bash
make check
```

等价于：

1. `python3 -m py_compile app/server.py app/deepseek.py netcheck-report.py`
2. 把 `app/index.html` 最后一段 `<script>` 抽出来做 `node --check`
3. 确认没有把下列文件提交进去：`netcheck-log*.csv`、`ai-chat.json`、`app/.deepseek_key`

**红线**：任何真实 IP、公网出口地址、DeepSeek 密钥、个人设备名都不允许进仓库。示例数据请用 `192.168.1.1` / `100.64.x.x` 这类占位值。

## 代码风格

- Python：PEP 8，4 空格，函数加简短中文 docstring 说明"为什么"而不是"做什么"
- 前端：单文件 `app/index.html`，内联 CSS/JS；不引入框架、不引入 CDN 资源
- 注释用中文，标识符用英文
- 新增图表前先问一句："这块信息能否并进现有的四个区块？" 能并就别加卡片

## 提交信息

采用 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/)：

```
feat(panel): 拓扑支持悬停查看历史最差值
fix(agent): 网关 IP 为空时回退到默认路由
docs(readme): 补充 DeepSeek 密钥配置说明
refactor(server): 抽出长 ping 任务调度
chore(ci): 增加 py_compile 校验
```

## Pull Request

- 一个 PR 只做一件事；涉及界面改动的，请附**改前/改后截图**
- 修改阈值口径（网关 5/10ms、阿里 40/100ms 等）必须同步更新 `docs/DEVELOPMENT.md` 的阈值表，否则面板与 AI 建议会打架
- 不要提交 `docs/screenshots/*.png` 之外的大体积二进制文件

## 报告问题

请附：`tailscale status` 输出（脱敏）、`sudo -n tailscale netcheck` 摘要、面板截图、以及 `var/panel.err` 的相关片段。
