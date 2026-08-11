# beeOS Makefile - 便捷命令
# 见 [技术架构 §3 部署架构]
#
# Linux only：macOS 开发者用 brew services 自行起 PG/Redis
# 无 Docker / 无 Node.js：本地开发 = systemctl 起 PG/Redis + uv run queen + nginx 服务静态 Portal

.PHONY: help install dev test lint format type-check up-services down-services \
        ps logs-queen clean dev-queen dev-portal \
        db-migrate db-init db-reset db-shell redis-shell \
        bee box-month-close seed-license deploy-check

# === 帮助 ===
help:  ## 显示所有命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	    awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# === 安装依赖 ===
install:  ## 安装所有 Python 依赖 (uv sync)
	uv sync --all-extras --dev

# === 开发环境（Linux/systemctl） ===
up-services:  ## 启动本机 PG + Redis（Queen 单独跑 dev-queen）
	systemctl start postgresql redis
	@echo "✅ PG + Redis 已就绪"
	@echo "   PG:    localhost:5432 (beeos/beeos-dev-password)"
	@echo "   Redis: localhost:6379"
	@echo "   下一步: make dev-queen"

down-services:  ## 停止本机 PG + Redis
	systemctl stop postgresql redis
	@echo "✅ PG + Redis 已停止"

ps:  ## 查看 4 个 systemd unit 状态
	systemctl status postgresql redis beeos-queen nginx --no-pager || true

# === 本地开发（不走 Docker） ===
dev-queen:  ## 本地启动 Queen（需要 PG / Redis 已起）
	uv run queen

dev-portal:  ## 打开 Portal（nginx 直服务静态文件，80 端口）
	@echo "Portal 是 nginx 服务的静态文件，无 dev 进程。"
	@echo "开发时直接编辑 apps/portal/*.html，浏览器打开 http://localhost/ 即可。"

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

db-init:  ## 初始化本机 PG：创建 beeos 用户和 beeos 库
	sudo -u postgres psql -f deploy/scripts/init-db.sql
	@echo "✅ PG 已就绪：localhost:5432 / beeos / beeos-dev-password"
	@echo "   下一步: uv run queen (启动时自动 create_all 建 6 张表)"

db-reset:  ## 删 beeos 库并重建（危险！会丢数据）
	sudo -u postgres psql -c "DROP DATABASE IF EXISTS beeos;" -c "DROP ROLE IF EXISTS beeos;"
	$(MAKE) db-init

db-shell:  ## PostgreSQL 交互 shell（走本机 PG）
	PGPASSWORD=beeos-dev-password psql -h localhost -U beeos -d beeos

redis-shell:  ## Redis 交互 shell（走本机 Redis，不走 Docker）
	redis-cli -h localhost

# === 服务（开发期随 Queen 跑） ===
bee:  ## Bee（M1 stub：随 Queen 进程启动）
	@echo "M1: Bee 随 Queen 启动，V1 拆为独立容器（暂无）"

box-month-close:  ## MonthCloseBox（M1 stub：随 Queen 进程启动）
	@echo "M1: MonthCloseBox 随 Queen 启动，V1 拆为独立容器（暂无）"

# === 部署辅助 ===
seed-license:  ## 生成测试 License（V1 实现）
	@echo "V1: License 生成器接入"

deploy-check:  ## 检查服务器是否满足部署要求
	bash deploy/scripts/check-server.sh
