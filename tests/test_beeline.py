"""tests.test_beeline - beeline 数据结构加载与图结构验证

覆盖范围：
- core.beeline_models 各组件 + 模型校验
- Beeline 顶层 + op_id 唯一 + next 引用 + DAG 检测
- beelines.inventory_shortage 库存不足 beeline 完整加载
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.beeline_models import (
    Bee,
    Beeline,
    ExternalSystem,
    Idempotency,
    NextRef,
    Operation,
)
from beelines import INVENTORY_SHORTAGE_BEELINE, get_inventory_shortage_beeline


# ============================================================
# Operation 资源校验
# ============================================================


class TestOperationValidation:
    def test_op_requires_resource(self):
        with pytest.raises(ValidationError, match="at least one of bee / external_system"):
            Operation(
                op_id="x",
                type="t",
                input_from="external",
                input="s",
                output="s",
                bee=None,
                external_system=None,
            )

    def test_op_with_bee_ok(self):
        op = Operation(
            op_id="x",
            type="t",
            input_from="external",
            input="s",
            output="s",
            bee=Bee(type="bee_x"),
        )
        assert op.bee.type == "bee_x"

    def test_op_with_external_system_ok(self):
        op = Operation(
            op_id="x",
            type="t",
            input_from="external",
            input="s",
            output="s",
            external_system=ExternalSystem(id="sys", interface="api"),
        )
        assert op.external_system.id == "sys"

    def test_idempotency_required_needs_key(self):
        with pytest.raises(ValidationError, match="idempotency.mode=required but no key"):
            Operation(
                op_id="x",
                type="t",
                input_from="external",
                input="s",
                output="s",
                bee=Bee(type="b"),
                idempotency=Idempotency(mode="required"),  # no key
            )

    def test_idempotency_forbidden_no_key_needed(self):
        op = Operation(
            op_id="x",
            type="t",
            input_from="external",
            input="s",
            output="s",
            bee=Bee(type="b"),
            idempotency=Idempotency(mode="forbidden"),
        )
        assert op.idempotency.mode == "forbidden"


# ============================================================
# Beeline 顶层 + 图结构校验
# ============================================================


class TestBeelineValidation:
    def _make_2_op_chain(self) -> Beeline:
        return Beeline(
            id="b1",
            version=1,
            description="simple chain",
            operations=[
                Operation(
                    op_id="a",
                    type="t",
                    input_from="external",
                    input="s",
                    output="s",
                    next=[NextRef(op_id="b")],
                    bee=Bee(type="b"),
                ),
                Operation(
                    op_id="b",
                    type="t",
                    input_from="a",
                    input="s",
                    output="s",
                    next=[],
                    bee=Bee(type="b"),
                ),
            ],
        )

    def test_simple_chain_ok(self):
        b = self._make_2_op_chain()
        assert b.id == "b1"

    def test_get_operation(self):
        b = self._make_2_op_chain()
        assert b.get_operation("a").op_id == "a"
        assert b.get_operation("missing") is None

    def test_get_start_operations_single_entry(self):
        b = self._make_2_op_chain()
        starts = b.get_start_operations()
        assert len(starts) == 1
        assert starts[0].op_id == "a"

    def test_duplicate_op_id_rejected(self):
        with pytest.raises(ValidationError, match="duplicate op_id"):
            Beeline(
                id="b1",
                version=1,
                description="x",
                operations=[
                    Operation(op_id="a", type="t", input_from="external",
                              input="s", output="s", next=[NextRef(op_id="b")], bee=Bee(type="b")),
                    Operation(op_id="a", type="t", input_from="external",
                              input="s", output="s", next=[NextRef(op_id="b")], bee=Bee(type="b")),
                    Operation(op_id="b", type="t", input_from="external",
                              input="s", output="s", next=[], bee=Bee(type="b")),
                ],
            )

    def test_next_to_unknown_op_rejected(self):
        with pytest.raises(ValidationError, match="unknown op_id 'missing'"):
            Beeline(
                id="b1",
                version=1,
                description="x",
                operations=[
                    Operation(op_id="a", type="t", input_from="external",
                              input="s", output="s", next=[NextRef(op_id="missing")], bee=Bee(type="b")),
                ],
            )

    def test_invalid_input_from_rejected(self):
        with pytest.raises(ValidationError, match="input_from='missing'"):
            Beeline(
                id="b1",
                version=1,
                description="x",
                operations=[
                    Operation(op_id="a", type="t", input_from="missing",
                              input="s", output="s", next=[], bee=Bee(type="b")),
                ],
            )

    def test_cycle_rejected(self):
        with pytest.raises(ValidationError, match="cycle"):
            Beeline(
                id="b1",
                version=1,
                description="x",
                operations=[
                    Operation(op_id="a", type="t", input_from="external",
                              input="s", output="s", next=[NextRef(op_id="b")], bee=Bee(type="b")),
                    Operation(op_id="b", type="t", input_from="a",
                              input="s", output="s", next=[NextRef(op_id="a")], bee=Bee(type="b")),
                ],
            )

    def test_diamond_fan_in_accepted(self):
        # diamond: a -> b, a -> c, b -> d, c -> d
        beeline = Beeline(
            id="diamond",
            version=1,
            description="diamond fan-in",
            operations=[
                Operation(op_id="a", type="t", input_from="external",
                          input="s", output="s",
                          next=[NextRef(op_id="b"), NextRef(op_id="c")], bee=Bee(type="b")),
                Operation(op_id="b", type="t", input_from="a",
                          input="s", output="s", next=[NextRef(op_id="d")], bee=Bee(type="b")),
                Operation(op_id="c", type="t", input_from="a",
                          input="s", output="s", next=[NextRef(op_id="d")], bee=Bee(type="b")),
                Operation(op_id="d", type="t", input_from="a",
                          input="s", output="s", next=[], bee=Bee(type="b")),
            ],
        )
        assert beeline.get_operation("d").op_id == "d"


# ============================================================
# beelines.inventory_shortage 集成验证
# ============================================================


class TestInventoryShortageBeeline:
    def test_module_exports(self):
        assert callable(get_inventory_shortage_beeline)
        assert isinstance(INVENTORY_SHORTAGE_BEELINE, Beeline)

    def test_id_and_version_match_definition_reference(self):
        # 必须与 boxes/inventory_shortage/definition.py 的引用一致
        # task[0].beeline_id == "beeline_inventory_shortage_v3"
        # task[0].beeline_version == 12
        b = get_inventory_shortage_beeline()
        assert b.id == "beeline_inventory_shortage_v3"
        assert b.version == 12

    def test_priority_chain_complete(self):
        # queen rules.exception_handling.inventory_shortage_priority 链必须全部出现
        b = get_inventory_shortage_beeline()
        op_ids = {op.op_id for op in b.operations}
        expected = {
            "diagnose_exception",
            "try_alternative_warehouse",
            "try_inter_warehouse_transfer",
            "try_replenishment_eta",
            "try_substitute_product",
            "calculate_compensation",
            "notify_customer",
            "escalate_to_human",
        }
        assert expected.issubset(op_ids)

    def test_branches_with_conditions(self):
        b = get_inventory_shortage_beeline()
        # 每个 try_* op 必须有 1 个有 when 的 next（success 分支）+ 1 个无 when 的 next（fallback）
        for op_id in (
            "try_alternative_warehouse",
            "try_inter_warehouse_transfer",
            "try_replenishment_eta",
            "try_substitute_product",
        ):
            op = b.get_operation(op_id)
            assert op is not None
            when_clauses = [n.when for n in op.next]
            assert any(w is not None for w in when_clauses), f"{op_id} missing conditional next"
            assert any(w is None for w in when_clauses), f"{op_id} missing unconditional next"

    def test_fan_in_at_notify_customer(self):
        # notify_customer 至少 4 个上游（4 个 try_* + calculate_compensation）
        b = get_inventory_shortage_beeline()
        targeted = {nxt.op_id for op in b.operations for nxt in op.next}
        # notify_customer 必须被 ≥ 4 个 op 指向
        upstreams = [op.op_id for op in b.operations
                     if any(n.op_id == "notify_customer" for n in op.next)]
        assert len(upstreams) >= 4

    def test_terminal_escalate_to_human(self):
        b = get_inventory_shortage_beeline()
        escalate = b.get_operation("escalate_to_human")
        assert escalate is not None
        # 升级人工是终态（无 next）
        assert escalate.next == []

    def test_idempotency_strategies(self):
        b = get_inventory_shortage_beeline()
        # notify_customer 必须 forbidden
        notify = b.get_operation("notify_customer")
        assert notify.idempotency.mode == "forbidden"
        # try_* 必须 required 且 key=order_id
        for op_id in (
            "try_alternative_warehouse",
            "try_inter_warehouse_transfer",
            "try_replenishment_eta",
            "try_substitute_product",
            "calculate_compensation",
        ):
            op = b.get_operation(op_id)
            assert op.idempotency.mode == "required", f"{op_id} should be required"
            assert op.idempotency.key == "order_id", f"{op_id} should use order_id key"

    def test_entry_diagnose_exception(self):
        b = get_inventory_shortage_beeline()
        # 入口必须只有 1 个，且 input_from=external
        starts = b.get_start_operations()
        assert len(starts) == 1
        assert starts[0].op_id == "diagnose_exception"
        assert starts[0].input_from == "external"

    def test_no_cycles(self):
        # 已在 Beeline model_validator 校验，再跑一遍确保数据真实无环
        b = get_inventory_shortage_beeline()
        # 简单 DFS 三色检测：白/灰/黑
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {op.op_id: WHITE for op in b.operations}

        def dfs(node: str) -> None:
            color[node] = GRAY
            op = b.get_operation(node)
            for nxt in op.next:
                if color[nxt.op_id] == GRAY:
                    raise AssertionError(f"cycle detected at {node} -> {nxt.op_id}")
                if color[nxt.op_id] == WHITE:
                    dfs(nxt.op_id)
            color[node] = BLACK

        for op in b.operations:
            if color[op.op_id] == WHITE:
                dfs(op.op_id)

        # 全部访问完成
        assert all(c == BLACK for c in color.values())