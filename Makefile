SHELL := /bin/sh
.DEFAULT_GOAL := help

VENV := backend/.venv
VENV_PY := $(shell if [ -x $(VENV)/bin/python ]; then echo "$(CURDIR)/$(VENV)/bin/python"; elif [ -x $(VENV)/Scripts/python.exe ]; then echo "$(CURDIR)/$(VENV)/Scripts/python.exe"; else echo python3; fi)
COMPOSE := docker compose -f docker/compose.yml

.PHONY: help setup install install-conda env-check release dev preview test lint openapi permission-check contract-check backup restore db-upgrade scheduler docker-build docker-up docker-down docker-logs reset

help:
	@echo "Fast CMS 常用命令"
	@echo "  make env-check       探测 Python / Miniconda / 端口"
	@echo "  make install         一键安装：后端环境 + .env + 初始化数据库"
	@echo "  make install-conda   用 Miniconda/Anaconda 环境承载后端后安装"
	@echo "  make setup           同 make install"
	@echo "  make dev             启动服务 http://localhost:8000/admin （热重载）"
	@echo "  make preview         按 .env 的 APP_HOST/APP_PORT 启动，关闭热重载"
	@echo "  make test            后端 pytest"
	@echo "  make lint            后端 ruff + mypy"
	@echo "  make openapi         导出 OpenAPI 到 openapi.json"
	@echo "  make contract-check  校验 OpenAPI 契约、权限覆盖与前后端页面连通性"
	@echo "  make release         发布门禁 + 生成 dist-release 发布包"
	@echo "  make backup          备份 SQLite 数据库"
	@echo "  make restore FILE=…  恢复 SQLite 备份"
	@echo "  make docker-build    构建单容器镜像"
	@echo "  make docker-up       启动 Compose 默认 SQLite 形态"
	@echo "  make reset           清空本地数据库与上传文件（需确认）"

env-check:
	$(VENV_PY) scripts/env_check.py

install:
	@sh install.sh

install-conda:
	@sh install.sh --use-conda

release:
	$(VENV_PY) scripts/release.py all

setup: install

dev:
	cd backend && $(VENV_PY) -m uvicorn app.main:app --host 127.0.0.1 --port $${APP_PORT:-8000} --reload

preview:
	cd backend && $(VENV_PY) -m uvicorn app.main:app --host $${APP_HOST:-127.0.0.1} --port $${APP_PORT:-8000}

test:
	cd backend && $(VENV_PY) -m pytest

lint:
	cd backend && $(VENV_PY) -m ruff check app tests ../scripts
	cd backend && $(VENV_PY) -m mypy app

openapi:
	cd backend && $(VENV_PY) -m app.cli export-openapi --output ../openapi.json

permission-check:
	cd backend && $(VENV_PY) -m app.cli check-permissions

contract-check: openapi permission-check
	$(VENV_PY) scripts/check_contract.py
	$(VENV_PY) scripts/check_api_coverage.py --strict

backup:
	cd backend && $(VENV_PY) -m app.cli backup

restore:
	@if [ -z "$(FILE)" ]; then echo "用法：make restore FILE=backend/data/backups/xxx.db"; exit 1; fi
	cd backend && $(VENV_PY) -m app.cli restore "$(FILE)" --yes

db-upgrade:
	cd backend && $(VENV_PY) -m app.cli db upgrade

scheduler:
	cd backend && $(VENV_PY) -m app.cli scheduler

docker-build:
	docker build -f docker/Dockerfile -t fastcms:local .

docker-up:
	$(COMPOSE) up -d --build

docker-down:
	$(COMPOSE) down

docker-logs:
	$(COMPOSE) logs -f app

reset:
	@printf "将删除本地 SQLite 数据库与上传文件，输入 RESET 确认："; read answer; \
	if [ "$$answer" = "RESET" ]; then \
		rm -f backend/data/fastcms.db backend/data/fastcms.db-wal backend/data/fastcms.db-shm; \
		rm -rf backend/data/uploads; \
		echo "[reset] 已清理，执行 make setup 重建"; \
	else echo "已取消"; fi
