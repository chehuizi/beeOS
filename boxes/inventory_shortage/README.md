# 订单异常履约盒（order_exception_box）

> 第一只业务履约盒子——按政策在 SLA 内给客户一个符合政策的可验证结果。

## 业务定位

订单履约过程中发生的异常（库存不足 / 支付失败 / 物流延迟 / 客户投诉 /
部分缺货），由本盒子统一处理。

业务语义是真的：
- **Contract**：6 条 acceptance（按 SLA / 通知 / 退款准确 / 订单状态一致 /
  证据完整 / 必要时升级人工）
- **Policy**：3 类 queen rules（task_routing / exception_handling /
  continuous_improvement）
- **Result**：resolution_result 业务结果

基础设施是假的（runtime / executor / worker 待下一阶段实现）。

## 数据形状

```mermaid
flowchart TB
    Def[BeeBoxDefinition]
    Def --> Schemas[schemas block]
    Def --> Task[task Intent]
    Def --> Result[result Contract]
    Def --> Queen[queen Policy]
    Def --> Metrics[metrics]

    Schemas --> S1[schema_order_request]
    Schemas --> S2[schema_address]
    Schemas --> S3[schema_order_exception]
    Schemas --> S4[schema_resolution_result]

    Task --> T1[type handle_order_exception]
    T1 --> T2[beeline_id v3]
    T1 --> T3[beeline_version 12]

    Result --> R1[6 acceptance rules]
    Result --> R2[4 exception rules]

    Queen --> Q1[authorization 3 档位]
    Queen --> Q2[rules 业务化字典]

    Metrics --> M1[quality 3 项]
    Metrics --> M2[latency 3 项]
    Metrics --> M3[cost 2 项]
```

## 文件

- `__init__.py` — 入口（导出 `get_definition` 与 `ORDER_EXCEPTION_BOX`）
- `schemas.py` — 4 个业务 schema 定义（order_request / address /
  order_exception / resolution_result）
- `definition.py` — 完整 BeeBoxDefinition 实例 + get_definition 函数

## 加载方式

```python
from boxes.inventory_shortage import get_definition

d = get_definition()
print(d.id, d.version)            # order_exception_box 3
print(d.get_schema("schema_order_exception"))
print(d.get_task("handle_order_exception"))
```

## 验收清单

```mermaid
flowchart LR
    Q1[4 个 schema 就位]
    Q2[1 个 task 含 beeline 引用]
    Q3[6 条 acceptance 覆盖 SLA/通知/退款/一致/证据/升级]
    Q4[4 条 exception 覆盖退款上限/企业客户/失败重试/客户拒绝]
    Q5[Queen 3 类授权 + 业务化规则]
    Q6[Metrics quality latency cost 8 项]
```

完整定义参考 `docs/examples/order-exception-box.md`。