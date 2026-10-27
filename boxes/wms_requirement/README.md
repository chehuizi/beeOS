# 需求捕获履约盒（wms_requirement_box）

> 把自然语言业务表述抽成可机械验证的结构化业务需求集——每条需求带原文溯源。

## 业务定位

链条的第一环。它回答的是：**业务方到底要什么**。

产出分两层：

- **机器结构**（`flow_graph` / `requirement_set_package`）——给下游盒子投料
- **人读文档**（`schema_requirement_document`）——给人看的那一份

两条来自**同一份事实**：文档是从流程图派生的投影，不是另造一份。

## 判据与例外

**4 条判据**：

| 判据 | 口径 |
|---|---|
| `contract_compliance` | 产出符合本合同声明的形状 |
| `source_coverage` | 原文每句都被认领（阈值 100%） |
| `trace_integrity` | 每条需求都追得到原文句子 |
| `no_hallucination` | 需求内容全部来自原文，无凭空生成 |

**2 条例外**：`requirement_count == 0` → 不交付；
`source_coverage < 50` → 升级（根本没覆盖住原文，不该静默交付）。

## beeline 5 ops

```
split_source_sentences → extract_requirements → build_requirement_set
  → verify_graph_fidelity → package_requirement_set
```

第 4 跳是**忠实度校验**：把抽取结果与流程图对一遍，
对不上的当场判红——这一跳在产出侧，不在验收侧。

## 人读投影

`document.py` 负责渲染：

- 条目 `REQ-001…`（描述 / 类型 / 优先级 / 原文出处 / 流程位置 / 验收标准占位）
- 需求 ↔ 原文追溯矩阵
- 未决项清单
- markdown 全文

两条刻意的规矩：

- **条目只从流程图派生**——平铺需求集会把一句话拆成碎片（「库存看板」单独成条）
- **验收标准只能占位**——本盒不判验收，它只标出「这条的验收标准还没写」

## 文件

- `__init__.py` — 入口（`get_definition`）
- `schemas.py` — 5 个 schema（含 `schema_requirement_document`）
- `definition.py` — BeeBoxDefinition 实例
- `runner.py` — 5 个 op 的 handler
- `document.py` — 需求文档投影（`build_document` / `render_markdown`）
- `measure.py` — 指标采集
- `beeline.py` — beeline_wms_requirement_v1

## 加载

```python
from boxes.wms_requirement import get_definition, get_beeline

box = get_definition()
beeline = get_beeline()
print(box.id, box.version, box.result.maturity)
```

## 已知边界

抽取条数在 2~7 条之间浮动，优先级目前统一是 `must_have`（**没有区分度**）。
这两点文档里都写明了——不假装它是确定的。
