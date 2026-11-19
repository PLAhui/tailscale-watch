SHELL := /bin/bash
PORT := 8767

.PHONY: help run stop install uninstall check report sample clean

help:
	@echo "make run        启动面板（前台，端口 $(PORT)）"
	@echo "make stop       停止面板进程"
	@echo "make install    安装 launchd（每 60s 采样 + 面板常驻）"
	@echo "make uninstall  卸载 launchd"
	@echo "make check      语法自检（Python + 前端 JS）"
	@echo "make report     命令行看最近 10 条采样"
	@echo "make sample     立刻采一条样本"
	@echo "make clean      清理运行时产物（保留数据）"

run:
	@mkdir -p var
	@/usr/bin/python3 app/server.py $(PORT)

stop:
	@bash scripts/stop-panel.command

install:
	@mkdir -p var
	@bash scripts/install-launchd.sh --port $(PORT)

uninstall:
	@bash scripts/uninstall-launchd.sh

check:
	@/usr/bin/python3 -m py_compile app/server.py app/deepseek.py netcheck-report.py && echo "python: ok"
	@python3 -c "import re,sys;h=open('app/index.html',encoding='utf-8').read();open('/tmp/_tw_check.js','w',encoding='utf-8').write(re.findall(r'<script>(.*?)</script>',h,re.S)[-1])"
	@node --check /tmp/_tw_check.js && echo "frontend js: ok"
	@test ! -f app/.deepseek_key || echo "warn: app/.deepseek_key 存在（别提交）"

report:
	@/usr/bin/python3 netcheck-report.py --tail 10

sample:
	@/bin/bash netcheck-watch.sh --once

clean:
	@rm -rf var/*.log var/*.out var/*.err __pycache__ app/__pycache__ && echo "已清理运行时日志"
