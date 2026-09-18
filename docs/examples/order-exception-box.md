# 示例：订单异常履约盒（Order Exception Fulfillment Box）

> **状态**：v0.1 示例
> **日期**：2026-09-18
> **对应**：[beebox-design.md](./beebox-design.md) · [beebox-gtm.md §2 Catalog](./beeos-gtm.md#2-履约盒子-catalog) · [beebox-definition-schema.md](./beebox-definition-schema.md) · [beebox-beeline-schema.md](./beebox-beeline-schema.md)

第一只履约盒子的完整示例。**订单异常履约盒**走通 beeOS 所有核心概念——验证 beeBox 设计模式可以承载真实业务责任。

---

## §1 业务背景

**盒子是什么**：

> 订单异常履约盒——接进去以后，它负责把订单异常从发现一直处理到闭环。

**业务责任**：订单发生异常（库存不足 / 支付失败 / 物流问题 / 客户投诉）时，按政策在 SLA 内给客户一个符合政策的可验证结果。

**典型异常**：

| 异常类型 | 业务结果 |
|---|---|
| 库存不足 | 客户获得商品（替代仓 / 跨仓调拨 / 补货 / 退款）|
| 支付失败 | 客户重新尝试或取消订单 |
| 物流延误 | 客户获得补偿或重新发货 |
| 客户投诉 | 客户得到回应和解决方案 |
| 部分缺货 | 客户获得部分商品 + 部分退款 |

**为什么是这个盒子**：天然包含 beeOS 设计的全部核心概念——Contract / Policy / Queen / Capability / BeeLine / TaskRun / Result / Acceptance / Evidence / Metrics。1 个盒子验证整个架构。

---

## §2 beeBox definition

5 维度 + 机制层完整 schema——这是 1 个真实的订单异常履约盒定义。

```yaml
beeBox_definition:
  # ============================================
  # 基础元信息
  # ============================================
  id: order_exception_box
  version: 3
  description: |
    订单异常履约盒——按政策处理订单异常，
    在 SLA 内给客户一个符合政策的可验证结果

  # ============================================
  # 数据形状（definition 自带 schema 列表）
  # ============================================
  schemas:
    - id: schema_order_request
      description: 订单请求数据
      fields:
        - name: order_id
          type: string
          required: true
        - name: customer_id
          type: string
          required: true
        - name: items
          type: array
          required: true
          items:
            type: object
            properties:
              - name: sku
                type: string
                required: true
              - name: quantity
                type: integer
                required: true
              - name: unit_price
                type: number
                required: true
        - name: shipping_address
          type: ref:schema_address
        - name: amount
          type: number
          required: true

    - id: schema_order_exception
      description: 订单异常事件
      fields:
        - name: exception_id
          type: string
          required: true
        - name: order_id
          type: string
          required: true
        - name: exception_type
          type: enum
          required: true
          # inventory_shortage / payment_failed / shipping_delay /
          # customer_complaint / partial_unavailable
        - name: detected_at
          type: string
          required: true
        - name: context
          type: object
          required: false
          properties:
            - name: severity
              type: enum
              required: false
              # low / medium / high / critical
            - name: customer_tier
              type: enum
              required: false
              # standard / vip / enterprise

    - id: schema_resolution_result
      description: 异常处理结果
      fields:
        - name: resolution_id
          type: string
          required: true
        - name: resolution_type
          type: enum
          required: true
          # fulfilled / partial_fulfilled / refunded / compensated / escalated
        - name: actions_taken
          type: array
          required: true
          items:
            type: object
            properties:
              - name: action
                type: string
                required: true
              - name: timestamp
                type: string
                required: true
        - name: refund_amount
          type: number
          required: false
        - name: customer_notified
          type: boolean
          required: true

  # ============================================
  # 履约意图（Intent）—— beeBox 接收什么 task
  # ============================================
  task:
    - type: handle_order_exception
      task_schema: schema_order_exception
      beeline_id: beeline_inventory_shortage_v3
      beeline_version: 12
      trigger: |
        库存系统检测到 inventory_shortage
        OR 支付系统推送 payment_failed
        OR 物流系统推送 shipping_delay
        OR 客服系统推送 customer_complaint

  # ============================================
  # 履约合同（Contract）—— 交付什么业务结果
  # ============================================
  result:
    type: order_exception_resolved
    result_schema: schema_resolution_result

    acceptance:                    # 验收标准（单次判据）
      - metric: resolution_within_sla
        op: lte
        # SLA 阈值：
        # VIP customer: 30min
        # Standard customer: 4h
        # Enterprise customer: 2h
        value: 30                   # 分钟（实际值按 customer_tier 动态计算）

      - metric: customer_notified
        op: eq
        value: true

      - metric: refund_amount_correct
        op: eq
        value: true
        # 如果有退款，金额必须 = 退款政策计算值

      - metric: order_state_consistent
        op: eq
        value: true
        # 订单最终态 = fulfilled / partial_fulfilled / refunded / compensated
        # 各系统订单状态必须一致

      - metric: evidence_complete
        op: eq
        value: true
        # 每次异常处理必须留完整 evidence（12 字段 + step log）

      - metric: human_escalation_if_needed
        op: eq
        value: true
        # 如果是 critical 或 > $500 退款，必须升级人工

    exceptions:                    # 例外条款
      - condition: refund_amount > 500
        action: escalate            # 升级人工审批
      - condition: customer_tier == enterprise AND severity == critical
        action: escalate            # 企业级客户 + 严重异常 → 人工
      - condition: resolution_failed_after_3_attempts
        action: rollback            # 3 次重试失败 → 撤销部分操作
      - condition: customer_complaint AND resolution_no_acceptable_option
        action: no_deliver          # 无可接受方案 → 不交付，标记 REJECTED

  # ============================================
  # 履约政策（Policy）—— queen 自治运营配置
  # ============================================
  queen:
    authorization:                 # 授权策略
      task_routing: allow            # 允许 queen 自动路由
      exception_handling: allow      # 允许 queen 自动处理异常
      continuous_improvement: observe_only  # 改善只观察不执行（避免擅自改规则）

    rules:
      task_routing:                 # 任务路由
        sla_by_customer_tier:
          vip: 30                    # VIP 30min
          standard: 240              # 标准 4h
          enterprise: 120            # 企业 2h

      exception_handling:           # 异常处理规则
        inventory_shortage_priority: # 库存不足优先级
          - try_alternative_warehouse
          - try_inter_warehouse_transfer
          - try_replenishment_eta
          - try_substitute_product
          - calculate_compensation
          - refund_or_recommend

        refund_auto_approval_threshold: 50    # < $50 自动退款
        refund_queen_judgment_range: [50, 500]  # $50~$500 queen 判断
        refund_human_approval_threshold: 500   # > $500 人工审批

        customer_complaint_response_time:     # 投诉响应时间
          vip: 15                            # VIP 15min
          standard: 60                      # 标准 1h

        escalation_rules:                    # 升级规则
          - trigger: severity == critical
            action: escalate_to_human
          - trigger: customer_tier == enterprise
            action: escalate_to_human
          - trigger: refund_amount > 500
            action: escalate_to_human

      continuous_improvement:       # 持续改善（observe_only）
        monitor_metrics:
          - first_time_resolution_rate
          - average_resolution_time
          - sla_breach_rate
          - human_escalation_rate
          - cost_per_exception

  # ============================================
  # 履约度量（Metrics）—— 评价盒子持续运行
  # ============================================
  metrics:
    quality:
      - name: first_time_resolution_rate
        definition: 首次处理解决率（不需要重试或升级）
        target: 0.85                # 目标 85%

      - name: sla_breach_rate
        definition: SLA 违背率
        target: 0.05                # 目标 < 5%

      - name: compensation_accuracy
        definition: 补偿金额准确率
        target: 0.99                # 目标 99%

    latency:
      - name: average_resolution_time
        definition: 平均处理时长
        target: 600                 # 目标 < 10 min（600s）

      - name: p95_resolution_time
        definition: 95 分位处理时长
        target: 1800                # 目标 < 30 min

      - name: first_response_time
        definition: 首次响应时间
        target: 60                   # 目标 < 1 min

    cost:
      - name: cost_per_exception
        definition: 单次异常处理成本（资源消耗 + 人工干预折算）
        target: 0.50                # 目标 < $0.50 / exception

      - name: human_escalation_rate
        definition: 升级人工率
        target: 0.15                # 目标 < 15%（85% 自动化）

  # ============================================
  # 机制层（不属于 Fulfillment 维度，是实现方式）
  # ============================================
  # task[].beeline_id 已在上方定义，引用独立 beeline 仓库
  # 这里不出现（schema 里只在 task 块引用 beeline_id + version）

  # Queen 自治边界（不可配置、queen 默认遵守）：
  # 可以：调整并发量 / 选择 procedure / retry 决策 / route 决策 /
  #       escalate / pause
  # 不能：修改 contract / 修改 acceptance / 修改 release / 修改 policy
```

---

## §3 机制层实现：beeline（库存不足处理 procedure）

beeline 是**独立维护**的对象，definition 只引用（`beeline_id` + `beeline_version`）。以下是 1 条真实的库存不足处理 beeline：

```yaml
beeline:
  id: beeline_inventory_shortage_v3
  version: 12
  description: 库存不足订单异常处理 procedure——按政策决策替代方案并执行

  operations:
    # ============================================
    # 起点：识别异常 + 提取上下文
    # ============================================
    - op_id: identify_exception
      type: extract_context
      input_from: external
      input: schema_order_exception
      output: schema_enriched_context
      next:
        - op_id: classify_severity
      bee:
        type: exception_classifier
      idempotency:
        mode: required
        key: "exception_id"

    # ============================================
    # 分流：按异常严重程度走不同路径
    # ============================================
    - op_id: classify_severity
      type: severity_router
      input_from: identify_exception
      output: schema_severity_decision
      next:
        - op_id: try_alternative_warehouse
          when: "severity in [low, medium]"
        - op_id: try_inter_warehouse_transfer
          when: "severity == high"
        - op_id: escalate_to_human
          when: "severity == critical"
      bee:
        type: severity_router

    # ============================================
    # 路径 1：低/中严重度 → 尝试替代仓
    # ============================================
    - op_id: try_alternative_warehouse
      type: check_inventory
      input_from: classify_severity
      output: schema_alternative_warehouse_result
      next:
        - op_id: reserve_substitute
          when: "result.available == true"
        - op_id: try_inter_warehouse_transfer
          when: "result.available == false"
      bee:
        type: inventory_query
      idempotency:
        mode: optional
        key: "sku"

    # ============================================
    # 路径 2：替代仓有库存 → 预留
    # ============================================
    - op_id: reserve_substitute
      type: inventory_reserve
      input_from: try_alternative_warehouse
      output: schema_reservation_result
      next:
        - op_id: calculate_compensation
      bee:
        type: inventory_writer
      idempotency:
        mode: required
        key: "substitute_sku"

    # ============================================
    # 路径 3：高严重度 → 跨仓调拨
    # ============================================
    - op_id: try_inter_warehouse_transfer
      type: check_transfer_options
      input_from: classify_severity
      output: schema_transfer_options
      next:
        - op_id: reserve_substitute
          when: "result.transfer_available == true"
        - op_id: try_replenishment_eta
          when: "result.transfer_available == false"
      bee:
        type: transfer_checker

    # ============================================
    # 路径 4：调拨也没有 → 查补货时间
    # ============================================
    - op_id: try_replenishment_eta
      type: check_replenishment
      input_from: try_inter_warehouse_transfer
      output: schema_replenishment_result
      next:
        - op_id: calculate_compensation
          when: "result.can_wait == true"
        - op_id: try_substitute_product
          when: "result.can_wait == false"
      bee:
        type: replenishment_checker

    # ============================================
    # 路径 5：查替代商品
    # ============================================
    - op_id: try_substitute_product
      type: find_substitute_product
      input_from: try_replenishment_eta
      output: schema_substitute_product_result
      next:
        - op_id: recommend_to_customer
          when: "result.substitute_available == true"
        - op_id: refund_or_recommend
          when: "result.substitute_available == false"
      bee:
        type: product_recommender

    # ============================================
    # 计算补偿（汇聚点）
    # ============================================
    - op_id: calculate_compensation
      type: compute_compensation
      input_from:
        - reserve_substitute
        - try_replenishment_eta
      output: schema_compensation_decision
      next:
        - op_id: execute_resolution
      bee:
        type: compensation_calculator
      idempotency:
        mode: required
        key: "order_id"

    # ============================================
    # 执行解决方案（人工升级 / 自动处理分支）
    # ============================================
    - op_id: execute_resolution
      type: execute_decision
      input_from: calculate_compensation
      output: schema_resolution_decision
      next:
        - op_id: auto_process
          when: "decision == auto_approve AND refund < 50"
        - op_id: queen_judge
          when: "decision == queen_review AND refund in [50, 500]"
        - op_id: escalate_to_human
          when: "decision == human_required OR refund > 500"
      bee:
        type: decision_router

    # ============================================
    # 自动处理（小金额）
    # ============================================
    - op_id: auto_process
      type: auto_execute
      input_from: execute_resolution
      output: schema_execution_result
      next:
        - op_id: notify_customer
      bee:
        type: refund_executor
      idempotency:
        mode: required
        key: "order_id"

    # ============================================
    # queen 判断（中金额）
    # ============================================
    - op_id: queen_judge
      type: queen_decision
      input_from: execute_resolution
      output: schema_queen_decision_result
      next:
        - op_id: auto_process
          when: "queen_decision == approve"
        - op_id: escalate_to_human
          when: "queen_decision == escalate"
      bee:
        type: queen_engine

    # ============================================
    # 升级人工
    # ============================================
    - op_id: escalate_to_human
      type: human_escalation
      input_from:
        - classify_severity
        - execute_resolution
        - queen_judge
        - recommend_to_customer
      output: schema_human_resolution_result
      next:
        - op_id: notify_customer
      bee:
        type: human_escalation_handler
      idempotency:
        mode: forbidden             # 不能重试（升级后等人工）

    # ============================================
    # 推荐给客户（替代商品）
    # ============================================
    - op_id: recommend_to_customer
      type: send_recommendation
      input_from: try_substitute_product
      output: schema_recommendation_sent_result
      next:
        - op_id: await_customer_response
      bee:
        type: notification_sender

    # ============================================
    # 等待客户响应（外部回调）
    # ============================================
    - op_id: await_customer_response
      type: wait_external
      input_from: recommend_to_customer
      output: schema_customer_response
      next:
        - op_id: notify_customer
          when: "customer_accepted"
        - op_id: refund_or_recommend
          when: "customer_rejected OR timeout"
      bee:
        type: async_waiter
      idempotency:
        mode: required
        key: "order_id"

    # ============================================
    # 退款或人工推荐
    # ============================================
    - op_id: refund_or_recommend
      type: refund_decision
      input_from:
        - try_substitute_product
        - await_customer_response
      output: schema_refund_decision
      next:
        - op_id: notify_customer
      bee:
        type: refund_calculator

    # ============================================
    # 通知客户（终点）
    # ============================================
    - op_id: notify_customer
      type: send_notification
      input_from:
        - auto_process
        - escalate_to_human
        - refund_or_recommend
        - await_customer_response
      output: schema_notification_result
      bee:
        type: notification_sender
      idempotency:
        mode: required
        key: "order_id"
```

**关键点**：
- **15 个 operation**——分叉 / 汇聚 / 异常升级 / 异步等待都体现
- **幂等性明确**——required / optional / forbidden 三类都有
- **人工升级是"机制层实现"**——queen 触发，handler 路由，不动 contract / policy

---

## §4 task run 12 字段履约事实

1 次真实的库存不足异常 task run——12 字段全部展开。

```json
{
  "task_run_id": "tr_a8f3d2e9-1b4c-4d5e-9f8a-7c6b5a4d3e2f",
  "created_at": "2026-09-18T10:15:30Z",
  "started_at": "2026-09-18T10:15:31Z",
  "finished_at": "2026-09-18T10:36:18Z",

  "originator": {
    "source": "inventory_system",
    "source_id": "inv_event_2026_09_18_001",
    "user": null
  },

  "intent": {
    "task_type": "handle_order_exception",
    "exception_id": "exc_2026_09_18_001",
    "exception_type": "inventory_shortage",
    "task_payload": {
      "order_id": "ord_12345",
      "customer_id": "cus_vip_789",
      "items": [
        { "sku": "SKU_A001", "quantity": 2, "unit_price": 89.00 }
      ],
      "amount": 178.00,
      "shipping_address": { ... }
    }
  },

  "contract_ref": {
    "definition_id": "order_exception_box",
    "definition_version": 3,
    "result_schema_id": "schema_resolution_result"
  },

  "policy_decision": [
    {
      "timestamp": "2026-09-18T10:18:42Z",
      "decision": "try_alternative_warehouse",
      "rule_applied": "inventory_shortage_priority[0]",
      "queen_authorization": "allow",
      "context": { "severity": "low", "customer_tier": "vip" }
    },
    {
      "timestamp": "2026-09-18T10:25:15Z",
      "decision": "calculate_compensation",
      "rule_applied": "compensation_calculator",
      "queen_authorization": "allow",
      "context": { "refund_amount": 0, "compensation_type": "free_shipping" }
    },
    {
      "timestamp": "2026-09-18T10:32:08Z",
      "decision": "auto_process",
      "rule_applied": "refund_auto_approval_threshold < 50",
      "queen_authorization": "allow",
      "context": { "refund_amount": 0 }
    }
  ],

  "beeBox_release_id": "order_exception_box@3.12.5",
  "beeLine_id": "beeline_inventory_shortage_v3",
  "beeLine_version": 12,
  "beeBox_instance_id": "instance_prod_aliyun_007",
  "runtime_id": "runtime_prod_aliyun",

  "result": {
    "resolution_id": "res_a8f3d2e9-...",
    "resolution_type": "fulfilled",
    "actions_taken": [
      { "action": "alternative_warehouse_reserved", "timestamp": "2026-09-18T10:19:05Z" },
      { "action": "compensation_calculated_free_shipping", "timestamp": "2026-09-18T10:25:15Z" },
      { "action": "customer_notified", "timestamp": "2026-09-18T10:36:15Z" }
    ],
    "refund_amount": 0,
    "customer_notified": true
  },

  "acceptance": {
    "status": "ACCEPTED",
    "checks": [
      { "metric": "resolution_within_sla", "expected": "30min", "actual": "20m48s", "pass": true },
      { "metric": "customer_notified", "expected": true, "actual": true, "pass": true },
      { "metric": "refund_amount_correct", "expected": true, "actual": true, "pass": true },
      { "metric": "order_state_consistent", "expected": true, "actual": true, "pass": true },
      { "metric": "evidence_complete", "expected": true, "actual": true, "pass": true },
      { "metric": "human_escalation_if_needed", "expected": true, "actual": "N/A", "pass": true }
    ]
  },

  "evidence": {
    "step_log": [
      {
        "op_id": "identify_exception",
        "started_at": "2026-09-18T10:15:31Z",
        "finished_at": "2026-09-18T10:15:32Z",
        "duration_ms": 1000,
        "input": { "exception_id": "exc_2026_09_18_001" },
        "output": { "exception_type": "inventory_shortage", "severity": "low", "customer_tier": "vip" },
        "status": "success"
      },
      {
        "op_id": "classify_severity",
        "started_at": "2026-09-18T10:15:32Z",
        "finished_at": "2026-09-18T10:18:42Z",
        "duration_ms": 190000,
        "input": { "severity": "low" },
        "output": { "next_op": "try_alternative_warehouse" },
        "status": "success"
      },
      {
        "op_id": "try_alternative_warehouse",
        "started_at": "2026-09-18T10:18:42Z",
        "finished_at": "2026-09-18T10:20:15Z",
        "duration_ms": 93000,
        "input": { "sku": "SKU_A001" },
        "output": { "available": true, "warehouse_id": "wh_002" },
        "status": "success"
      },
      {
        "op_id": "reserve_substitute",
        "started_at": "2026-09-18T10:20:15Z",
        "finished_at": "2026-09-18T10:22:08Z",
        "duration_ms": 113000,
        "input": { "substitute_sku": "SKU_A001", "warehouse_id": "wh_002" },
        "output": { "reservation_id": "resv_xyz" },
        "status": "success"
      },
      {
        "op_id": "calculate_compensation",
        "started_at": "2026-09-18T10:25:15Z",
        "finished_at": "2026-09-18T10:25:16Z",
        "duration_ms": 1000,
        "input": { "order_id": "ord_12345" },
        "output": { "compensation_type": "free_shipping", "refund_amount": 0 },
        "status": "success"
      },
      {
        "op_id": "execute_resolution",
        "started_at": "2026-09-18T10:30:08Z",
        "finished_at": "2026-09-18T10:32:08Z",
        "duration_ms": 120000,
        "input": { "decision": "auto_approve" },
        "output": { "next_op": "auto_process" },
        "status": "success"
      },
      {
        "op_id": "auto_process",
        "started_at": "2026-09-18T10:32:08Z",
        "finished_at": "2026-09-18T10:33:12Z",
        "duration_ms": 64000,
        "input": { "order_id": "ord_12345" },
        "output": { "execution_result": "fulfilled" },
        "status": "success"
      },
      {
        "op_id": "notify_customer",
        "started_at": "2026-09-18T10:33:12Z",
        "finished_at": "2026-09-18T10:36:18Z",
        "duration_ms": 186000,
        "input": { "customer_id": "cus_vip_789", "message": "Your order has been fulfilled from alternative warehouse with free shipping compensation" },
        "output": { "notification_sent": true, "channel": "email" },
        "status": "success"
      }
    ]
  },

  "cost": {
    "compute_units": 12,
    "external_api_calls": 8,
    "human_minutes": 0,
    "estimated_cost_usd": 0.18
  },

  "latency": {
    "triggered_to_started_ms": 1000,
    "execution_duration_ms": 1247000,
    "started_to_finished_ms": 1247000,
    "sla_target_ms": 1800000,
    "sla_breached": false
  },

  "exceptions": []
}
```

**关键点**：
- **12 字段全部展开**——任何一个字段都能独立审计
- **status 状态机**：triggered → running → COMPLETED → AWAITING_ACCEPTANCE → ACCEPTED
- **policy_decision 含 3 次 queen 决策**——可追溯
- **evidence 含 8 个 step log**——完整执行轨迹
- **cost + latency 量化**——商业指标可直接采集

---

## §5 Acceptance 流程

Acceptance 阶段独立于 task run 执行——task run COMPLETED 后进入 AWAITING_ACCEPTANCE，按 6 条规则判定。

```yaml
acceptance_process:
  trigger: task_run.status == COMPLETED

  state_machine:
    AWAITING_ACCEPTANCE:           # task run 完成后进入
      action: evaluate_acceptance_rules
      next:
        - ACCEPTED: all_rules_passed
        - REJECTED: any_critical_rule_failed
        - COMPENSATING_ACCEPTANCE: rule_failed_but_recoverable

  ACCEPTED:                        # 履约成功
    side_effects:
      - update_order_status: "fulfilled"
      - notify_customer: "resolution complete"
      - emit_metrics: [quality, latency, cost]
      - close_exception: true

  REJECTED:                        # 履约失败
    side_effects:
      - trigger_compensation: true  # 进入 COMPENSATING_ACCEPTANCE
      - escalate_to_human: "if critical"
      - emit_alert: "fulfillment_rejected"

  COMPENSATING_ACCEPTANCE:         # 验收触发补偿
    actions:
      - reverse_failed_operations
      - execute_alternative_resolution
      - re_enter_acceptance_loop

# 6 条 acceptance 规则（跟 §2 result.acceptance 对应）：
rules:
  - name: resolution_within_sla
    evaluate: "latency.started_to_finished_ms <= sla_target_ms"
    # sla_target_ms 按 customer_tier 动态：
    #   vip = 1800000 (30min)
    #   standard = 14400000 (4h)
    #   enterprise = 7200000 (2h)

  - name: customer_notified
    evaluate: "result.customer_notified == true"

  - name: refund_amount_correct
    evaluate: "result.refund_amount == policy_calculated_refund"
    skip_if: "result.refund_amount == 0"     # 无退款时跳过

  - name: order_state_consistent
    evaluate: "order_state == result.resolution_type"

  - name: evidence_complete
    evaluate: "evidence.step_log.length >= 5 AND policy_decision.length >= 1"

  - name: human_escalation_if_needed
    evaluate: "if critical: human_escalated == true"
```

**关键点**：
- **Acceptance 独立阶段**——task run COMPLETED ≠ Business Fulfillment 成功
- **6 条规则**全部来自 result.acceptance，可独立判定
- **3 个终态**：ACCEPTED / REJECTED / COMPENSATING_ACCEPTANCE（跟 §3.4.6 一致）
- **侧效应**——ACCEPTED 触发业务状态更新 / 指标埋点 / 异常关闭

---

## §6 Queen 决策示例

queen 在该次 task run 中做了 3 次决策，全部记录在 `policy_decision` 字段。

### 决策 1：路由（severity = low → 走 try_alternative_warehouse）

```yaml
queen_decision:
  timestamp: "2026-09-18T10:18:42Z"
  type: task_routing
  decision: try_alternative_warehouse
  rule_applied: inventory_shortage_priority[0]   # 第一个尝试
  authorization: allow
  reasoning: |
    severity = low，customer_tier = vip
    按 inventory_shortage_priority 规则，第一个动作是查替代仓
  queen_engine_version: "queen_engine_v1.4.2"
  bounded_autonomy_check: "✓ 未越权（未改 contract / policy / acceptance）"
```

### 决策 2：计算补偿（补偿类型 = free_shipping）

```yaml
queen_decision:
  timestamp: "2026-09-18T10:25:15Z"
  type: exception_handling
  decision: free_shipping_compensation
  rule_applied: compensation_calculator
  authorization: allow
  reasoning: |
    替代仓有库存 → 订单可以从 wh_002 发出
    VIP 客户 → 补偿升级到 free_shipping（高于普通 standard 客户的 standard_shipping）
  queen_engine_version: "queen_engine_v1.4.2"
  bounded_autonomy_check: "✓ 未越权（按 customer_tier 选择补偿，未改 refund 政策）"
```

### 决策 3：自动处理（refund = 0 < 50）

```yaml
queen_decision:
  timestamp: "2026-09-18T10:32:08Z"
  type: exception_handling
  decision: auto_process
  rule_applied: refund_auto_approval_threshold < 50
  authorization: allow
  reasoning: |
    refund_amount = 0 < 50
    按 refund_auto_approval_threshold 规则，自动处理不需要人工审批
  queen_engine_version: "queen_engine_v1.4.2"
  bounded_autonomy_check: "✓ 未越权（按阈值路由，未超越自动处理授权）"
```

### Queen 不能做的事（边界示例）

```
✗ Queen 不能改 customer_tier = vip 这个事实（contract 定义）
✗ Queen 不能改 refund_auto_approval_threshold = 50（policy 定义）
✗ Queen 不能改 acceptance 规则（contract 的一部分）
✗ Queen 不能改 refund_amount 计算逻辑（policy 的一部分）
✗ Queen 不能拒绝 SLA 30min 要求（contract 定义）

如果需要改这些，必须：
  - 升级人工审批
  - 修改 definition（beeBox owner 操作）
  - 发布新 release（不可变升级）
```

---

## §7 Certification（Fulfillment Box Certification）

按 [beeos-gtm.md §3 Certification](./beeos-gtm.md#3-成熟度-certificationfulfillment-box-certification) 9 项验证：

```
订单异常履约盒 v3.12.5
───────────────
Business Result                ✓ 明确（resolution_type = fulfilled/partial/refunded/...）
Acceptance                      ✓ 明确（6 条规则，详见 §5）
SLA                             ✓ 99.5%（30min / 4h / 2h 按 customer_tier 分级）
Quality                         ✓ 85%（first_time_resolution_rate 目标）
Exception Coverage              ✓ 94%（85% 自动 + 6% 人工升级 + 9% 异常触发补偿）
Human Escalation                ✓ 支持（refund > 500 / critical / enterprise）
Evidence                        ✓ 完整（12 字段 + step log + queen decision trace）
Rollback                        ✓ 支持（COMPENSATING_ACCEPTANCE 触发反向操作）
Observability                   ✓ 完整（kanban 看板实时展示 12 字段 + Metrics）
Production Runs                 ✓ 1,200,000（示意：上线 6 个月，月均 200K）
```

**Certified Fulfillment Box**——成熟度可验证，可作为品牌资产。

---

## §8 跟设计稿的对照

| 示例对象 | design 章节 |
|---|---|
| **definition 5 维度 + 机制层** | [§3.1.2 数据结构](./beebox-design.md#31-beeBox-definition) · [§2.1 Fulfillment 语义结构](./beebox-design.md#21-履约合同) |
| **beeline 编排（15 ops）** | [beebox-beeline-schema.md](./beebox-beeline-schema.md) · [§3.4.3 operation 执行](./beebox-design.md#344-operation-执行) |
| **task run 12 字段履约事实** | [§2.4 task run 履约记录](./beebox-design.md#24-task-run-履约记录) · [§3.4.1 task run 触发 + durable object](./beebox-design.md#341-task-run-触发--durable-object) |
| **9 状态机** | [§3.4.4 异常处理 + task run 状态机](./beebox-design.md#344-异常处理--task-run-状态机) |
| **Acceptance 阶段（3 终态）** | [§3.4.6 Acceptance 阶段](./beebox-design.md#346-acceptance-阶段业务验收--执行完成) |
| **Queen 决策 + bounded autonomy** | [§3.4.7 Queen 自治边界](./beebox-design.md#347-queen-自治边界bounded-autonomy) |
| **Catalog + Certification** | [beeos-gtm.md §2 / §3](./beeos-gtm.md) |

---

## §9 验证清单

这个示例验证了 beeOS 设计的全部核心概念：

| 概念 | 验证位置 |
|---|---|
| **Fulfillment 5 维度**（Intent / Contract / Policy / Result / Acceptance）| §2 definition 顶层 |
| **机制层独立**（Procedure / BeeLine）| §2 mechanism 层 + §3 beeline |
| **task run 12 字段履约事实** | §4 完整 JSON |
| **task run 9 状态机**（triggered/running/COMPLETED/AWAITING_ACCEPTANCE/ACCEPTED...）| §4 + §5 状态转换 |
| **Acceptance 独立阶段** | §5 完整流程 |
| **Queen bounded autonomy**（can-do / cannot-do）| §6 决策示例 |
| **Metrics 度量**（quality / latency / cost 3 维度）| §2 metrics |
| **Catalog 盒子设计**（输入 / 输出 / 责任 / SLA / 成本 / 证据）| §1 + §7 |
| **Certification 9 项** | §7 Certification 卡片 |

**这个示例证明**：beeOS 设计可以承载真实业务责任，订单异常履约盒可以跑通 beeBox → release → instance → task run → Acceptance → 证据 → 指标的全链路。

下一步：把这个示例变成实际可执行的 beeBox（implementation）。