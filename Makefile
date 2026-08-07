# beeOS Makefile - 便捷命令
# 见 [技术架构 §3 部署架构]

.PHONY: help install dev test lint format type-check up down logs clean \
        queen bee box-month-close portal \
        db-migrate db-shell redis-shell \
        seed-license

# === 帮助 ===
help:  ## 显示所有命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	    awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# === 安装依赖 ===
install:  ## 安装所有 Python 依赖 (uv sync)
	uv sync --all-extras --dev
	cd apps/portal && npm install

# === 开发环境 ===
up:  ## 启动 Docker Compose 开发环境
	docker compose up -d
	@echo "✅ BeeOS 已启动"
	@echo "   Portal:  http://localhost:3000"
	@echo "   Queen:   http://localhost:8080/health"
	@echo "   PG:      localhost:5432 (beeos/beeos-dev-password)"
	@echo "   Redis:   localhost:6379"

down:  ## 停止 Docker Compose
	docker compose down

logs:  ## 查看所有服务日志
	docker compose logs -f

logs-queen:  ## 查看 Queen 日志
	docker compose logs -f queen

logs-portal:  ## 查看 Portal 日志
	docker compose logs -f portal

clean:  ## 删除所有容器 + 数据卷 (危险!)
	docker compose down -v
	rm -rf .venv apps/portal/node_modules apps/portal/.next

# === 本地开发（不走 Docker） ===
dev-queen:  ## 本地启动 Queen（需要 PG / Redis 已跑）
	uv run queen

dev-portal:  ## 本地启动 Portal
	cd apps/portal && npm run dev

# === 代码质量 ===
lint:  ## Ruff lint
	uv run ruff check .

format:  ## Ruff auto-format
	uv run ruff format .

type-check:  ## mypy
	uv run mypy packages apps

test:  ## pytest
	uv run pytest

test-cov:  ## pytest + coverage
	uv run pytest --cov=packages --cov=apps --cov-report=html

# === 数据库 ===
db-migrate:  ## 应用数据库迁移（待实现）
	@echo "TODO: alembic 迁移（V1 接入）"

db-shell:  ## PostgreSQL 交互 shell
	docker compose exec postgres psql -U beeos beeos

redis-shell:  ## Redis 交互 shell
	docker compose exec redis redis-cli

# === 服务容器 ===
queen:  ## 仅启动 Queen 容器
	docker compose up -d queen

bee:  ## 启动 Bee 容器（M1 stub）
	@echo "M1: Bee 随 Queen 启动，V1 拆为独立容器"

box-month-close:  ## 启动 MonthCloseBox
	@echo "M1: MonthCloseBox 随 Queen 启动，V1 拆为独立容器"

portal:  ## 仅启动 Portal
	docker compose up -d portal

# === 部署辅助 ===
seed-license:  ## 生成测试 License（V1 实现）
	@echo "V1: License 生成器接入"

# === 1 人天部署脚本（M1 占位） ===
deploy-check:  ## 检查服务器是否满足部署要求
	bash deploy/scripts/check-server.sh
