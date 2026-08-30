# _shelved/

M0 阶段暂时不用的代码。**不是删了，是 mark 留着**，V1+ 调度层/持久化接入时再恢复。

## 什么时候恢复

| 触发 | 恢复目录 |
|---|---|
| 第一个真客户要 demo Dashboard | `_shelved/queen/`（FastAPI + dispatcher） |
| 跨进程 Job 追踪 / 并发需求 | `_shelved/beeos_core/db.py` + `models.py` |
| 多 Bee 并行 + 持久化 | 同时恢复 queen + hive |
| 商业化合规审计 | `_shelved/beeos_core/guardian.py`（JWT + 凭证加密） |
| 跑老 Queen API 测试 | `_shelved/tests/test_queen_api.py` |

## 目录结构

```
_shelved/
├── queen/                  # FastAPI 调度服务（M0 不用）
│   ├── pyproject.toml
│   └── queen/
│       ├── __main__.py     # CLI 入口
│       ├── api/app.py      # /api/v0/{jobs,audit,runtime,overview}
│       ├── core/
│       │   ├── dispatcher.py
│       │   ├── state_machine.py
│       │   └── audit.py    # 哈希链审计
├── beeos_core/             # 跨服务共享（M0 不用 ORM/AES/JWT）
│   ├── db.py               # SQLAlchemy 2.0 async session
│   ├── models.py           # 6 张表 ORM
│   └── guardian.py         # AES-256-GCM + JWT + 注入检测
└── tests/
    └── test_queen_api.py   # Queen API 集成测试
```

## M0 还能用的 beeos_core

M0 只保留 2 个轻量模块，够 Bee + Box 跑：

- `beeos_core.config` — pydantic-settings（BEEOOS_ 前缀，环境变量）
- `beeos_core.logging` — structlog 包装

完整模块在 `_shelved/beeos_core/` 里。
