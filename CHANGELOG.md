# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added
- `scripts/install-launchd.sh` / `uninstall-launchd.sh`，用 `__PROJECT_DIR__` 占位符渲染 plist，仓库可移植
- `Makefile`（`make run` / `make install` / `make check` / `make report`）
- `docs/DEVELOPMENT.md`：可复刻整套系统的开发提示词（含阈值口径与踩坑清单）
- `CONTRIBUTING.md`、`LICENSE`（MIT）、`.gitignore`

### Changed
- 目录开源化重排：`docs/` `scripts/` `launchd/`，截图移入 `docs/screenshots/`
- launchd label 统一为 `io.github.tailscale-watch.{agent,panel}`
- 面板默认端口统一为 8767（可用 `PANEL_PORT` 覆盖）
- `start-panel.command` 改为固定端口 + `curl -sf` 判活，不再自动漂端口

### Removed
- 页面里用于自测的 `#lp` / `#lpview` / `#ai` 调试钩子
- 首版 6 张 KPI 卡、分布/抖动双图并排、明细表直出等冗余区块的失败尝试（改为四块布局 + 折叠）

## [0.9.0] - 2026-11-19

### Added
- 本地 Web 面板：结论条 / 链路拓扑 / 延迟趋势 / 明细（折叠）
- 60 秒采样 daemon，16 列 CSV（含路由器网关段）
- 链路拓扑 SVG + JS `requestAnimationFrame` 光点动效
- 「长 ping 诊断」：三目标并行 40 包，输出 min/avg/max/stddev
- 「AI 分析」：DeepSeek 接入，结论/依据/执行方案/只读命令四段式，支持多轮追问与 SSE 流式输出
- 中文命令行报表 `netcheck-report.py`

### Fixed
- 端口漂移导致页面读到旧实例（P50 全部显示「—」）
- ECharts 局部 `setOption` 导致图表白屏
- 删除 DOM 区块后残留 JS 引用导致整页渲染中断
- 折叠区图表初始宽度为 0
