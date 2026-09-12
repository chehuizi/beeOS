# beeOS Makefile — 设计稿阶段
#
# 设计稿：
#   docs/beebox-design.md   — beeBox 单体设计（v0.1 交付单元）
#
# 当前目标：设计稿定稿，零代码
#
# 命令：
#   make verify-mermaid  # 校验设计稿里的 mermaid 块结构合法

.PHONY: help verify-mermaid

help:  ## 显示所有命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

verify-mermaid:  ## 校验设计稿里的 mermaid 块
	@python3 scripts/verify_mermaid.py docs/beebox-design.md
