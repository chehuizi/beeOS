# beeOS Makefile - M0 阶段（Bee + BeeBox，无 Queen / 无 Hive）
#
# 旧 M1 命令（dev-queen / up-services / db-init 等）已废弃，
# V1 恢复 Queen 时从 _shelved/queen/ 重新接回。
#
# 跑通 M0 只需：
#   make install      # uv sync
#   make dev-box      # 单跑 Box（不依赖 Bee）
#   make dev-bee      # Bee 加载 Box 跑全流程 + 写审计
#   make test         # 跑全部测试
#   make smoke        # 一行命令验证端到端

.PHONY: help install dev test lint format type-check \
        dev-box dev-bee smoke clean \
        bee-list box-manifest

# === 帮助 ===
help:  ## 显示所有命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# === 安装 ===
install:  ## 安装所有 Python 依赖 (uv sync)
	uv sync --all-extras --dev

# === M0 本地开发 ===
dev-box:  ## 跑 MonthCloseBox（不依赖 Bee，最小调试）
	uv run month-close --period 2026-07

dev-bee:  ## 跑 Bee + MonthCloseBox 端到端（写本地审计）
	uv run bee --box month_close --period 2026-07

bee-list:  ## 列出所有已注册 Box
	uv run bee --list

box-manifest:  ## 打印 MonthCloseBox 的 manifest
	uv run month-close --manifest

smoke:  ## 一行端到端：装包 + 跑 Box + 跑 Bee + 测
	@echo "=== 1. Box 独立跑 ===" && uv run month-close --period 2026-07
	@echo ""
	@echo "=== 2. Bee 加载 Box 跑 ===" && uv run bee --box month_close --period 2026-07
	@echo ""
	@echo "=== 3. pytest ===" && uv run pytest -q

# === 代码质量 ===
lint:  ## Ruff lint
	uv run ruff check .

format:  ## Ruff auto-format
	uv run ruff format .

type-check:  ## mypy
	uv run mypy packages apps

# === 测试 ===
test:  ## 跑全部测试
	uv run pytest

test-cov:  ## 跑测试 + coverage
	uv run pytest --cov=packages --cov=apps --cov-report=html

# === 清理 ===
clean:  ## 清理缓存和审计日志
	rm -rf .ruff_cache .pytest_cache .mypy_cache logs/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
