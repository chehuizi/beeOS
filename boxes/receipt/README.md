# 小票识别履约盒（receipt_capture_box）

> 把一张收银小票照片解析成结构化小票——模型只负责看图抄字，算术一律由盒子自己算。

## 业务定位

票据识别是典型的「**语义靠模型、事实靠机器**」：

- **模型**：看图抄出商户名、单号、时间、每一行商品与金额
- **盒子**：自己加总行金额、自己核对合计、自己验日期格式

模型抄错一个数字会立刻被算术判据抓住；模型自己声明「合计对得上」则一分不值。

## 判据与例外

**6 条判据**全部是机械算术，不含任何主观项：

| 判据 | 口径 |
|---|---|
| `field_completeness` | 必填字段一个不缺 |
| `line_arithmetic` | 每行：单价 × 数量 == 行金额 |
| `sum_arithmetic` | 行金额之和 == 小计 |
| `total_arithmetic` | 小计 + 税 == 合计 |
| `date_wellformed` | 时间格式合法且可解析 |
| `totals_match` | 票面合计 == 重算合计 |

**2 条例外**：`items_count == 0` → 不交付（没东西可交）；
`totals_match == false` → 升级人工（对不上就有人拍板，不猜）。

## beeline 3 ops

```
read_receipt → verify_receipt → package_receipt
```

`read_receipt` 看图抄字，`verify_receipt` 跑上面的算术，
`package_receipt` 组装 `schema_receipt_parsed`。

## 文件

- `__init__.py` — 入口（`get_definition`）
- `schemas.py` — `schema_receipt_photo` / `schema_receipt_item` / `schema_receipt_parsed`
- `definition.py` — BeeBoxDefinition 实例
- `runner.py` — 3 个 op 的 handler
- `measure.py` — 指标采集
- `beeline.py` — beeline_receipt_capture_v1

## 加载

```python
from boxes.receipt import get_definition, get_beeline

box = get_definition()
beeline = get_beeline()
print(box.id, box.version, box.result.maturity)
```

## 边界

本盒**未归域**（`placement.domain` 为空）——票据识别还没划进哪个业务域，
但合同、判据、例外都已就位并注册进看板。
