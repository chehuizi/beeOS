"""tests.test_definition - Definition 数据结构加载验证

覆盖范围：
- core.models 各组件的 pydantic 验证（含 FieldDef 类型结构规则）
- BeeBoxDefinition 顶层结构 + get_schema / get_task 查询方法
- boxes.inventory_shortage 库存不足盒子 Definition 完整加载
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.models import (
    AcceptanceRule,
    BeeBoxDefinition,
    ExceptionRule,
    FieldDef,
    MetricDef,
    MetricsDef,
    QueenAuthorization,
    QueenDef,
    QueenRules,
    ResultDef,
    SchemaDef,
    TaskDef,
)
from boxes.inventory_shortage import ORDER_EXCEPTION_BOX, get_definition


# ============================================================
# FieldDef 类型结构验证
# ============================================================


class TestFieldDef:
    def test_string_field_ok(self):
        f = FieldDef(name="order_id", type="string")
        assert f.required is True  # 默认必填

    def test_optional_field(self):
        f = FieldDef(name="refund_amount", type="number", required=False)
        assert f.required is False

    def test_object_requires_properties(self):
        with pytest.raises(ValidationError, match="must have properties"):
            FieldDef(name="bad", type="object")

    def test_array_requires_items(self):
        with pytest.raises(ValidationError, match="must have items"):
            FieldDef(name="bad", type="array")

    def test_nested_object_and_array_ok(self):
        f = FieldDef(
            name="items",
            type="array",
            items=FieldDef(
                name="item",
                type="object",
                properties=[FieldDef(name="sku", type="string")],
            ),
        )
        assert f.items.properties[0].name == "sku"

    def test_ref_type_allowed(self):
        f = FieldDef(name="shipping_address", type="ref:schema_address")
        assert f.type == "ref:schema_address"


# ============================================================
# SchemaDef
# ============================================================


class TestSchemaDef:
    def test_basic_schema(self):
        s = SchemaDef(
            id="schema_x",
            fields=[FieldDef(name="a", type="string")],
        )
        assert s.id == "schema_x"
        assert len(s.fields) == 1


# ============================================================
# TaskDef / AcceptanceRule / ExceptionRule / ResultDef
# ============================================================


class TestTaskDef:
    def test_task_references_schema_and_beeline(self):
        t = TaskDef(
            type="handle_x",
            task_schema="schema_x",
            beeline_id="beeline_x",
            beeline_version=1,
        )
        assert t.beeline_id == "beeline_x"
        assert t.beeline_version == 1
        assert t.trigger is None


class TestAcceptanceRule:
    def test_valid_ops(self):
        for op in ["gte", "lte", "eq", "in", "match"]:
            r = AcceptanceRule(metric="m", op=op, value=1)
            assert r.op == op

    def test_invalid_op_rejected(self):
        with pytest.raises(ValidationError):
            AcceptanceRule(metric="m", op="bogus", value=1)


class TestExceptionRule:
    def test_valid_actions(self):
        for action in ["no_deliver", "rollback", "escalate"]:
            r = ExceptionRule(condition="c", action=action)
            assert r.action == action

    def test_invalid_action_rejected(self):
        with pytest.raises(ValidationError):
            ExceptionRule(condition="c", action="explode")


class TestResultDef:
    def test_result_with_acceptance_and_exceptions(self):
        r = ResultDef(
            type="order_resolved",
            result_schema="schema_result",
            acceptance=[AcceptanceRule(metric="m1", op="eq", value=True)],
            exceptions=[ExceptionRule(condition="c1", action="escalate")],
        )
        assert len(r.acceptance) == 1
        assert len(r.exceptions) == 1


# ============================================================
# QueenDef / QueenAuthorization / QueenRules
# ============================================================


class TestQueenDef:
    def test_default_authorization(self):
        a = QueenAuthorization()
        assert a.task_routing == "allow"
        assert a.continuous_improvement == "observe_only"

    def test_off_authorization_allowed(self):
        a = QueenAuthorization(
            task_routing="off",
            exception_handling="observe_only",
        )
        assert a.task_routing == "off"

    def test_invalid_authorization_rejected(self):
        with pytest.raises(ValidationError):
            QueenAuthorization(task_routing="yolo")

    def test_rules_optional(self):
        q = QueenDef(authorization=QueenAuthorization(), rules=QueenRules())
        assert q.rules.task_routing is None
        assert q.rules.exception_handling is None
        assert q.rules.continuous_improvement is None

    def test_rules_business_dict(self):
        q = QueenDef(
            authorization=QueenAuthorization(),
            rules=QueenRules(
                task_routing={"sla_by_customer_tier": {"vip": 30}},
                exception_handling={"refund_threshold": 50},
            ),
        )
        assert q.rules.task_routing["sla_by_customer_tier"]["vip"] == 30


# ============================================================
# MetricsDef / MetricDef
# ============================================================


class TestMetricsDef:
    def test_defaults_empty(self):
        m = MetricsDef()
        assert m.quality == []
        assert m.latency == []
        assert m.cost == []

    def test_metric_target_required(self):
        m = MetricDef(name="rate", definition="first time rate", target=0.85)
        assert m.target == 0.85


# ============================================================
# BeeBoxDefinition 顶层 + 查询方法
# ============================================================


class TestBeeBoxDefinition:
    def _make_minimal_definition(self) -> BeeBoxDefinition:
        return BeeBoxDefinition(
            id="box_x",
            version=1,
            description="minimal box",
            schemas=[
                SchemaDef(
                    id="schema_in",
                    fields=[FieldDef(name="order_id", type="string")],
                ),
                SchemaDef(
                    id="schema_out",
                    fields=[FieldDef(name="ok", type="boolean")],
                ),
            ],
            task=[
                TaskDef(
                    type="do_x",
                    task_schema="schema_in",
                    beeline_id="beeline_x",
                    beeline_version=1,
                ),
            ],
            result=ResultDef(
                type="x_resolved",
                result_schema="schema_out",
                acceptance=[AcceptanceRule(metric="ok", op="eq", value=True)],
                exceptions=[
                    ExceptionRule(condition="bad", action="no_deliver"),
                ],
            ),
            queen=QueenDef(
                authorization=QueenAuthorization(),
                rules=QueenRules(),
            ),
            metrics=MetricsDef(),
        )

    def test_minimal_definition_ok(self):
        d = self._make_minimal_definition()
        assert d.id == "box_x"
        assert d.version == 1

    def test_get_schema_found_and_missing(self):
        d = self._make_minimal_definition()
        assert d.get_schema("schema_in").id == "schema_in"
        assert d.get_schema("schema_missing") is None

    def test_get_task_found_and_missing(self):
        d = self._make_minimal_definition()
        assert d.get_task("do_x").type == "do_x"
        assert d.get_task("task_missing") is None

    def test_required_fields_enforced(self):
        # 缺少 task 必须报错
        with pytest.raises(ValidationError):
            BeeBoxDefinition(
                id="box_x",
                version=1,
                description="x",
                result=ResultDef(
                    type="x",
                    result_schema="schema_x",
                    acceptance=[],
                    exceptions=[],
                ),
                queen=QueenDef(
                    authorization=QueenAuthorization(),
                    rules=QueenRules(),
                ),
            )


# ============================================================
# boxes.inventory_shortage 集成验证
# ============================================================


class TestInventoryShortageBox:
    def test_module_exports(self):
        # 入口 get_definition 必须可用
        assert callable(get_definition)
        # 常量 ORDER_EXCEPTION_BOX 必须是 BeeBoxDefinition
        assert isinstance(ORDER_EXCEPTION_BOX, BeeBoxDefinition)

    def test_get_definition_returns_instance(self):
        d = get_definition()
        assert isinstance(d, BeeBoxDefinition)
        assert d.id == "order_exception_box"
        assert d.version >= 1

    def test_schemas_complete(self):
        d = get_definition()
        ids = {s.id for s in d.schemas}
        # 4 个核心 schema 必须就位
        assert "schema_order_request" in ids
        assert "schema_address" in ids
        assert "schema_order_exception" in ids
        assert "schema_resolution_result" in ids

    def test_task_uses_existing_schema(self):
        d = get_definition()
        t = d.get_task("handle_order_exception")
        assert t is not None
        # task 引用的 schema 必须真实存在
        assert d.get_schema(t.task_schema) is not None
        # beeline 引用（机制层独立，但必须在 task 内）
        assert t.beeline_id.startswith("beeline_")
        assert t.beeline_version >= 1

    def test_result_uses_existing_schema(self):
        d = get_definition()
        assert d.get_schema(d.result.result_schema) is not None

    def test_acceptance_rules_complete(self):
        d = get_definition()
        # 至少 5 条 acceptance（含 SLA / 通知 / 退款 / 一致性 / 证据）
        assert len(d.result.acceptance) >= 5
        metrics = {a.metric for a in d.result.acceptance}
        assert "resolution_within_sla" in metrics
        assert "customer_notified" in metrics
        assert "evidence_complete" in metrics

    def test_exception_rules_have_valid_actions(self):
        d = get_definition()
        valid_actions = {"no_deliver", "rollback", "escalate"}
        for e in d.result.exceptions:
            assert e.action in valid_actions

    def test_queen_authorization_within_bounds(self):
        d = get_definition()
        a = d.queen.authorization
        for v in (a.task_routing, a.exception_handling, a.continuous_improvement):
            assert v in {"allow", "observe_only", "off"}

    def test_queen_rules_business_specific(self):
        d = get_definition()
        rules = d.queen.rules
        # 订单异常盒必须有 SLA 分级策略
        assert rules.task_routing is not None
        assert "sla_by_customer_tier" in rules.task_routing
        # 库存不足优先级链必须存在
        assert rules.exception_handling is not None
        assert "inventory_shortage_priority" in rules.exception_handling

    def test_metrics_three_dimensions(self):
        d = get_definition()
        # quality / latency / cost 三类度量都必须有
        assert len(d.metrics.quality) >= 1
        assert len(d.metrics.latency) >= 1
        assert len(d.metrics.cost) >= 1
        # 所有 metric 必须有 name 和 target
        for group in (d.metrics.quality, d.metrics.latency, d.metrics.cost):
            for m in group:
                assert m.name
                assert m.target >= 0