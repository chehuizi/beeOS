# beeOS

> Business Fulfillment Operating System — 业务履约操作系统

beeOS 是一个面向业务的履约操作系统：把一次业务履约（business fulfillment）
建模为可验证的产物，而不是一段胶水脚本。

## 三句话架构宣言

- **beeOS**：提供让业务履约可被定义、可被运行、可被验收的基础设施
- **beeBox**：单一业务履约盒子的完整定义 + 实现 + 验收契约
- **Business Fulfillment fulfills under explicit contract and policy,
  producing verifiable result**：履约按明确合同与政策发生，
  产出可被验收的业务结果

## 仓库结构

```mermaid
flowchart TB
    Repo[beeOS repo]
    Repo --> Docs[docs/ design]
    Repo --> Core[core/ kernel]
    Repo --> Boxes[boxes/ products]
    Repo --> Runtime[runtime/ executor]
    Repo --> Tests[tests/ verification]

    Docs --> D1[beebox-design]
    Docs --> D2[beebox-definition-schema]
    Docs --> D3[beebox-beeline-schema]
    Docs --> D4[beebox-runtime-design]
    Docs --> D5[beebox-runtime-schema]
    Docs --> D6[beeos-gtm]
    Docs --> D7[examples/order-exception-box]

    Core --> C1[models.py Definition shapes]
    Boxes --> B1[inventory_shortage Order Exception Box]
    Tests --> T1[test_definition.py]
```

## 布局说明

- **`docs/`** — 设计稿（5 文件 schema + GTM + 示例盒子）
  - `beebox-design.md` — 主设计稿（架构宣言 / 核心契约 / 执行语义 / 实现设计）
  - `beebox-definition-schema.md` — beeBox Definition 数据形状
  - `beebox-beeline-schema.md` — BeeLine / Operation 数据形状
  - `beebox-runtime-design.md` — Runtime 设计
  - `beebox-runtime-schema.md` — Runtime 数据形状
  - `beeos-gtm.md` — 商业定位 / Catalog / Certification / 飞轮
  - `examples/order-exception-box.md` — 订单异常履约盒完整示例

- **`core/`** — 基础设施内核
  - `models.py` — Definition / Schema / Field / Task / Result / Acceptance /
    Exception / Queen / Metrics 顶层数据结构（pydantic）

- **`boxes/`** — 业务履约盒子（产品层）
  - `inventory_shortage/` — 第一只盒子：订单异常履约
  - 每只盒子包含：`__init__.py` / `schemas.py` / `definition.py`

- **`runtime/`** — 执行运行时（PoC 0 占位）

- **`tests/`** — 验证
  - `test_definition.py` — Definition 数据结构加载验证

## PoC 0 目标

第一阶段不实现完整 runtime，只把核心数据形状 + 第一只盒子 Definition
落地成可加载、可验证的 pydantic 模型。

业务语义必须是真的（policy / contract / acceptance 完整定义）；
基础设施可以是假的（runtime / executor / worker 都是 mock，下一阶段实现）。

## 当前状态

```mermaid
flowchart LR
    Done[已完成]
    Done --> S1[核心数据结构 pydantic]
    Done --> S2[库存不足盒子 schemas]
    Done --> S3[库存不足盒子 definition]
    Done --> S4[34 个测试通过]
    Done --> S5[清理 + README]
```

下一阶段：beeLine / Operation 实现 + Runtime 最小闭环（mock executor）。

## 开发

```bash
# Python 3.11+
.venv/bin/python -m pytest tests/ -v

# 加载第一只盒子
.venv/bin/python -c "from boxes.inventory_shortage import get_definition; d = get_definition(); print(d.id, d.version)"
```