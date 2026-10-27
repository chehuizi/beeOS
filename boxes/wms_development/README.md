# WMS 开发履约盒（wms_development_box）

> **按设计包改代码**——把方案变成能构建、能跑起来的代码。

## 业务定位

链条的第三环。它回答的是：**这条需求怎么变成真代码**。

判据跑的是**外部神谕**：编译器、仓库自带的测试套件、git——
模型说什么不算数。

## 4 跳 beeline

```
dev_intake → dev_code → dev_build → build_packager
```

| 跳 | 干什么 |
|---|---|
| `dev_intake` | 设计包 → 本次要动的源码位置 |
| `dev_code` | 在隔离 worktree 里真改代码（模型写片段，机械改文件）|
| `dev_build` | 编译 + 跑仓库自带测试 + 连编两遍比 `target/classes` 摘要 |
| `build_packager` | 组装制品 + 评审覆盖 / 可追溯判定 |

## 三条关键机制

这三条都是真跑撞出来的：

1. **「已实现」必须有机械证据**——每个 WMS 概念登记了「文件落点 + 标识符」证据，
   标识符不在指定文件里 → 概念判为待实现。模型交白卷（零改动）→ 原样回喂重做。
   **模型的自述不是证据。**
2. **编译错误是最好的回喂材料**——只摘 `[ERROR] COMPILATION ERROR` 那一段，
   完整 maven 日志几千行会把模型淹掉。
3. **重试必须刷新上下文**——保留上一轮改动 + 让模型看见自己改坏的那一行。
   只给「改动前」的源码，它看不见自己写错的行，修不了。

## 判据与例外

**4 条判据**：

| 判据 | 口径 |
|---|---|
| `build_reproducibility` | 连编两遍的 `target/classes` 摘要一致 |
| `unit_test_green` | 仓库自带测试套件退出码 0、零失败 |
| `review_coverage` | 改动落在方案覆盖的位置内，且至少一个业务文件被改 |
| `artifact_traceable` | 制品 sha256 + git commit + 构建日志三者绑定 |

**2 条例外**：构建失败 → 不交付；单测通过率不足 → 升级。

> 例外动作不用 rollback：runtime 的 rollback 只是个状态标记，没人真补偿——
> 说「回滚」而不真回滚，是另一种形式的假信号。

## 隔离与可追溯

- **工作区必须隔离**：`git worktree`，不直接在主仓改
- **改动落在主仓分支**：`beeos/dev-<sha>`——交付物必须追得回去
- **可复现摘要取 `target/classes`**，不取 jar（jar 带时间戳）

## 环境变量

| 变量 | 作用 |
|---|---|
| `BEEOS_WMS_REPO` | WMS 仓库路径 |
| `BEEOS_MVN` | maven 可执行文件路径 |
| `BEEOS_DEV_WORKSPACE` | worktree 根目录（默认 `/tmp/beeos-dev`）|

## 文件

- `__init__.py` — 入口（`get_definition`）
- `schemas.py` — 6 个 schema
- `definition.py` — BeeBoxDefinition 实例
- `code_map.py` — 概念 → 源码落点 + 实现证据
- `mapper_binding.py` — 设计包 ↔ 源码位置绑定
- `codegen.py` — worktree 准备 / 上下文采集 / 片段替换 / 提交
- `build.py` — 编译 / 单测 / 可复现性
- `review.py` — 覆盖度与可追溯判定
- `runner.py` — 4 个 op 的 handler
- `measure.py` — 指标采集
- `beeline.py` — beeline_wms_development_v1
