# beeOS Makefile — 设计稿阶段
#
# 设计稿：
#   docs/beebox-design.md            — beeBox 单体设计（v0.1 交付单元）
#   docs/beebox-definition-schema.md — beeBox definition 结构化 schema 规格（含 queen 配置 + 业务 schema 形状）
#   docs/beebox-beeline-schema.md    — beeline 结构化 schema 规格
#   docs/beebox-runtime-design.md    — runtime 平台设计
#   docs/beebox-runtime-schema.md    — runtime 资源对象 schema 规格
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
	@python3 scripts/verify_mermaid.py docs/beebox-definition-schema.md
	@python3 scripts/verify_mermaid.py docs/beebox-beeline-schema.md
	@python3 scripts/verify_mermaid.py docs/beebox-runtime-design.md
	@python3 scripts/verify_mermaid.py docs/beebox-runtime-schema.md
