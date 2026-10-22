"""tests/test_subject_ref.py - 履约记录要能说清「这次是关于谁的」

归档要按「工作单元」（一个需求集 / 一次运维任务）分组变化。
前提是履约记录里**有一个可索引的字段**指向它处理的业务对象。

原来**没有任何字段**能做这件事：
- `intent` 是 task type
- `originator` 是触发源
- 业务标识只活在 artifact payload 里，没被索引

所以「按需求分组」只能靠事后从 payload 里刨 `set_id`——那不是索引，是碰运气。

现在：`TaskDef.subject_ref_field` 由盒子声明投料里哪个字段是业务对象标识，
运行时抽出来固进 `TaskRunIdentity.subject_ref`，并索引进记录。

这组守卫盯四件事：
1. 声明了就不能是空话——字段必须在 schema 里，且投料里必须真的给
2. 值的形状必须是 id 字符串，不是整份对象
3. 抽出来的必须真的落进记录、能查得到
4. 归档门槛：只有 accepted 的才配进档案
"""

from __future__ import annotations

import pytest

from kanban.trigger import BOX_REGISTRY, TriggerError, trigger_task
from runtime.store import TaskRunStore


DECLARING = {
    bid: t.subject_ref_field
    for bid, (loader, _) in BOX_REGISTRY.items()
    for t in loader().task
    if t.subject_ref_field
}


class TestTheDeclarationIsReal:
    """声明了就不能是空话"""

    def test_the_declared_field_exists_in_the_task_schema(self):
        """声明的字段必须在 task_schema 里，否则 trigger 每次都拒

        字段名打错字 → 每次履约都抛 TriggerError → 这只盒子彻底不能用。
        而「声明了但字段不存在」在定义加载时完全看不出来——
        所以这条要在测试里钉住。
        """
        for bid, (loader, _) in BOX_REGISTRY.items():
            definition = loader()
            for task in definition.task:
                if not task.subject_ref_field:
                    continue
                schema = definition.get_schema(task.task_schema)
                assert schema is not None, f"{bid}: task_schema 不存在"
                names = {f.name for f in schema.fields}
                assert task.subject_ref_field in names, (
                    f"{bid}/{task.type}: subject_ref_field="
                    f"{task.subject_ref_field!r} 不在 task_schema "
                    f"{task.task_schema!r} 的字段里（{sorted(names)}）"
                )

    def test_a_box_that_declares_it_must_have_a_value_in_the_payload(self):
        """声明了就必须真的给——缺了要拒绝，不许静默降级成空

        缺 subject_ref 的履约在归档里归不到任何工作单元，
        而没人会注意到「少了一条记录」——那正是「算了却看不见」那一族。
        """
        for bid, (loader, _) in BOX_REGISTRY.items():
            definition = loader()
            for task in definition.task:
                if not task.subject_ref_field:
                    continue
                schema = definition.get_schema(task.task_schema)
                payload = _minimal_payload(schema)
                payload[task.subject_ref_field] = ""  # 空串 = 没给
                with pytest.raises(TriggerError) as exc:
                    trigger_task(bid, task.type, payload, TaskRunStore("/tmp/_sr.jsonl"))
                assert task.subject_ref_field in str(exc.value), (
                    f"{bid}/{task.type}: 报错没点名缺的是哪个字段"
                )

    def test_the_value_must_be_an_id_string_not_a_whole_object(self):
        """值必须是 id 字符串——传整份对象要被拒

        两道闸都可能拦住它，这都对：
        - 字段声明成 `string` 的 → runtime/contract.py 的类型校验先挡下
        - 字段声明成 `ref:` 的 → 不做深校验，传什么都能过，
          得由 trigger 里「必须是 id 字符串」这条显式拦住

        不管哪道闸拦的，结果都一样：不能放一份对象进去，
        否则 subject_ref 会变成对象的 repr，
        归档里出现 `{'package_id': ...}` 这种东西。
        """
        checked = 0
        for bid, (loader, _) in BOX_REGISTRY.items():
            definition = loader()
            for task in definition.task:
                if not task.subject_ref_field:
                    continue
                schema = definition.get_schema(task.task_schema)
                payload = _minimal_payload(schema)
                payload[task.subject_ref_field] = {"id": "x", "whole": "object"}
                store = TaskRunStore("/tmp/_sr_obj.jsonl")
                before = len(store.list_recent(limit=1000))
                try:
                    trigger_task(bid, task.type, payload, store)
                except (TriggerError, ValueError):
                    # 被拒了——这是对的结局。哪一层拦的不重要：
                    # - trigger 里的 isinstance 检查（错误信息更懂业务）
                    # - pydantic 的 Optional[str]（类型层兜底）
                    checked += 1
                    continue
                except Exception as exc:
                    # 崩了（AttributeError 之类）——这**不对**：
                    # 传错形状的投料应该被明确拒绝，不是让运行时炸掉。
                    raise AssertionError(
                        f"{bid}/{task.type}: 投料形状不对时抛的是 "
                        f"{type(exc).__name__} 而不是 TriggerError——"
                        f"调用方拿到的是一个看不懂的堆栈"
                    )
                after = store.list_recent(limit=1000)
                assert len(after) == before, (
                    f"{bid}/{task.type}: 传了整份对象居然被放行了"
                )
                for r in after:
                    if r.get("subject_ref") is not None:
                        assert isinstance(r["subject_ref"], str), (
                            f"{bid}/{task.type}: 记录里的 subject_ref 是 "
                            f"{type(r['subject_ref']).__name__}——"
                            f"整份对象的 repr 进了档案，归档就没法按它分组了"
                        )
                checked += 1
        assert checked >= 3, (
            f"只有 {checked} 只盒子声明了 subject_ref_field——"
            "这条守卫得覆盖足够多的盒子才有意义"
        )

    def test_a_box_that_does_not_declare_it_gets_no_subject_ref(self):
        """没声明的盒子，subject_ref 就是 None——不猜

        猜（按字段名找 set_id / requirement_ref / xxx_id）等于把业务约定
        写进内核，加一只盒子就得改内核。

        投料里**故意带字符串**——不然「猜」根本猜不出东西，
        这条守卫就成了摆设（踩过：wms_system_box 投料是空的，
        变异「按字段名猜」完全无效，守卫不会红）。
        """
        store = TaskRunStore("/tmp/_sr_none.jsonl")
        result = trigger_task(
            "wms_system_box",
            "sync_system_profile",
            {"target_base_url": "http://127.0.0.1:1"},
            store,
        )
        run = store.get_run(result["task_run_id"])
        assert run["subject_ref"] is None, (
            f"没声明 subject_ref_field 的盒子被猜出了业务对象："
            f"{run['subject_ref']!r}"
        )


class TestTheRefIsRecordedAndFindable:
    """抽出来的必须真的落进记录、能查得到"""

    def test_the_model_level_type_is_what_actually_holds_the_line(self):
        """`subject_ref` 在模型层就声明成 `Optional[str]`——那是真正的防线

        运行时那条 isinstance 检查是**更好的报错**（告诉调用方「要 id 字符串，
        不是那份对象」），但真正保证「记录里不会有一份对象」的，是类型注解。

        把注解放宽成 `Optional[Any]`，防线就没了——
        所以这条守卫直接断言注解本身。
        """
        import typing

        from runtime.models import TaskRunIdentity

        ann = TaskRunIdentity.model_fields["subject_ref"].annotation
        assert ann == typing.Optional[str], (
            f"TaskRunIdentity.subject_ref 的注解是 {ann}，"
            f"应该是 Optional[str]——放宽它，整份对象就能进记录了"
        )

    def test_the_ref_lands_in_the_record(self, tmp_path):
        store = TaskRunStore(tmp_path / "runs.jsonl")
        result = trigger_task(
            "wms_design_box",
            "handle_modeling_request",
            _minimal_payload(_schema_of("wms_design_box", "handle_modeling_request"))
            | {"set_id": "REQ-2026-003"},
            store,
        )
        run = store.get_run(result["task_run_id"])
        assert run["subject_ref"] == "REQ-2026-003", (
            f"记录里的 subject_ref 是 {run['subject_ref']!r}"
        )

    def test_one_work_unit_pulls_up_all_its_runs(self, tmp_path):
        """一个需求集串起的那几次履约，要能被一次查出来

        这就是「按需求分组」的最小能力：档案写回时按 subject_ref 取，
        就得到这个单元名下的全部事实。
        """
        store = TaskRunStore(tmp_path / "runs.jsonl")
        schema = _schema_of("wms_design_box", "handle_modeling_request")
        for i in range(3):
            trigger_task(
                "wms_design_box",
                "handle_modeling_request",
                _minimal_payload(schema) | {"set_id": "REQ-2026-003"},
                store,
            )
        trigger_task(
            "wms_design_box",
            "handle_modeling_request",
            _minimal_payload(schema) | {"set_id": "REQ-2026-999"},
            store,
        )

        mine = store.list_by_subject("REQ-2026-003")
        assert len(mine) == 3, f"同单元的履约只查到 {len(mine)} 条"
        assert all(r["subject_ref"] == "REQ-2026-003" for r in mine)
        # 别人的单元不串进来
        assert len(store.list_by_subject("REQ-2026-999")) == 1
        assert store.list_by_subject("REQ-不存在") == []

    def test_accepted_only_gates_what_may_enter_the_archive(self, tmp_path):
        """归档门槛：accepted_only 只收验过的

        不验就写回去 = 把「说过的东西」当成「事实」记下来。
        档案是下一个需求的地面，地面上的错会一路传下去。

        这里断言的是**过滤器的不变量**，不断言某只盒子会通过——
        「哪只盒子的最小投料能过验收」是业务问题，会变；
        「accepted_only 收进来的每一条都必须是 accepted」不是。
        """
        store = TaskRunStore(tmp_path / "runs.jsonl")
        schema = _schema_of("wms_design_box", "handle_modeling_request")
        trigger_task(
            "wms_design_box", "handle_modeling_request",
            _minimal_payload(schema) | {"set_id": "REQ-X"}, store,
        )

        everything = store.list_by_subject("REQ-X")
        gated = store.list_by_subject("REQ-X", accepted_only=True)
        assert everything, "连不带过滤的都查不到——subject_ref 索引本身坏了"
        assert set(r["task_run_id"] for r in gated) <= set(
            r["task_run_id"] for r in everything
        ), "加了过滤反而多出了记录"
        assert all(r["acceptance_status"] == "accepted" for r in gated), (
            f"过滤后混进了没验过的：{[r['acceptance_status'] for r in gated]}"
        )


# ============================================================
# 辅助
# ============================================================


def _schema_of(box_id: str, task_type: str):
    definition = BOX_REGISTRY[box_id][0]()
    task = next(t for t in definition.task if t.type == task_type)
    return definition.get_schema(task.task_schema)


def _minimal_payload(schema) -> dict:
    """按 schema 造一份最小合法投料（required 字段都给标量占位）"""
    payload: dict = {}
    for f in schema.fields:
        if not f.required:
            continue
        if f.type == "array":
            payload[f.name] = []
        elif f.type == "object":
            payload[f.name] = {}
        elif f.type == "boolean":
            payload[f.name] = False
        elif f.type == "integer":
            payload[f.name] = 0
        elif f.type == "number":
            payload[f.name] = 0
        else:
            payload[f.name] = "x"
    return payload
