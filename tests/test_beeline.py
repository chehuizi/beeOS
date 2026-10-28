"""tests.test_beeline - beeline 数据结构加载与图结构验证

覆盖范围：
- core.beeline_models 各组件 + 模型校验
- Beeline 顶层 + op_id 唯一 + next 引用 + DAG 检测
- 全部已注册盒子的 beeline 结构不变式
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
# 全部已注册盒子的 beeline 结构不变式
# ============================================================


def _registered_beelines():
    """已注册盒子 → 它声明的 beeline（从注册表推，不另立一张表）"""
    from kanban.trigger import BOX_REGISTRY

    out = []
    for box_id, (get_def, beeline_loaders) in BOX_REGISTRY.items():
        definition = get_def()
        for task in definition.task:
            out.append((box_id, definition, beeline_loaders[task.beeline_id]()))
    return out


class TestEveryRegisteredBoxBeeline:
    """不变式，不是某一只盒子的快照

    以前这里断言的是订单异常盒的优先级链 / fan-in / 幂等策略——
    那只盒子退役时，这组断言也跟着退役了。
    换成**跨产品的不变式**：每只已注册盒子的 beeline 都得满足，
    盒子增删都不影响它。
    """

    def test_exactly_one_start_op_and_it_is_external(self):
        for box_id, _definition, beeline in _registered_beelines():
            starts = beeline.get_start_operations()
            assert len(starts) == 1, f"{box_id}: 入口 op 不止 1 个"
            assert starts[0].input_from == "external", f"{box_id}: 入口 op 不是从外部投料"

    def test_exactly_one_terminal_op(self):
        for box_id, _definition, beeline in _registered_beelines():
            terminals = [op for op in beeline.operations if not op.next]
            assert len(terminals) == 1, f"{box_id}: 终态 op 不止 1 个"

    def test_no_cycles(self):
        for box_id, _definition, beeline in _registered_beelines():
            WHITE, GRAY, BLACK = 0, 1, 2
            color = {op.op_id: WHITE for op in beeline.operations}

            def dfs(node: str) -> None:
                color[node] = GRAY
                op = beeline.get_operation(node)
                for nxt in op.next:
                    if color[nxt.op_id] == GRAY:
                        raise AssertionError(
                            f"{box_id}: 有环 {node} -> {nxt.op_id}"
                        )
                    if color[nxt.op_id] == WHITE:
                        dfs(nxt.op_id)
                color[node] = BLACK

            for op in beeline.operations:
                if color[op.op_id] == WHITE:
                    dfs(op.op_id)
            assert all(c == BLACK for c in color.values()), f"{box_id}: 没走遍"

    def test_every_op_declares_an_idempotency_policy(self):
        """有副作用的 op 必须声明幂等策略。

        只读盒子（小票识别：读 → 核 → 打包，全是纯计算）不需要——
        所以这条只查**有副作用**的那些盒子。
        """
        for box_id, definition, beeline in _registered_beelines():
            if definition.placement.domain != "WMS":
                continue
            for op in beeline.operations:
                assert op.idempotency is not None, (
                    f"{box_id}: op {op.op_id} 没声明幂等策略"
                )

    def test_branching_boxes_declare_a_condition(self):
        """有多个 next 的 op，至少一个 next 带 when

        「多路 next 却全不带条件」= 声明了分支但实际走死第一条。
        """
        for box_id, _definition, beeline in _registered_beelines():
            for op in beeline.operations:
                if len(op.next) > 1:
                    assert any(n.when is not None for n in op.next), (
                        f"{box_id}: op {op.op_id} 有多个 next 但没有条件分支"
                    )

