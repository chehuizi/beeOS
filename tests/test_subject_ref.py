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

from core.models import FieldDef
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
                payload = _minimal_payload(schema, definition.schemas)
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
        - 字段声明成 `ref:` 的 → 形状校验（现在做深校验了）先挡下，
          万一没挡住，还有 trigger 里「必须是 id 字符串」这条显式拦截

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
                payload = _minimal_payload(schema, definition.schemas)
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
                _minimal_payload(schema, _schemas_of("wms_design_box")) | {"set_id": "REQ-2026-003"},
                store,
            )
        trigger_task(
            "wms_design_box",
            "handle_modeling_request",
            _minimal_payload(schema, _schemas_of("wms_design_box")) | {"set_id": "REQ-2026-999"},
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
            _minimal_payload(schema, _schemas_of("wms_design_box")) | {"set_id": "REQ-X"}, store,
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
# 标识藏在对象里（subject_ref_subfield）
# ============================================================


class TestTheRefCanLiveInsideAnObject:
    """`ref:` 字段装的是整份对象，标识在对象的某个键上

    以前只有 `subject_ref_field` 一个字段，它只能指向「直接就是 id」的字段。
    于是 dev / testing 这两只**收对象**的盒子要么拿不到 subject_ref
    （归档里归不到任何工作单元），要么被迫把整份对象塞进去
    （subject_ref 变成一份对象的 repr）。两条都是「声明了却拿不到」。
    """

    @pytest.fixture
    def lax_dev_box(self, monkeypatch):
        """一只「契约很松」的开发盒：投料字段只声明 package_id，不声明 set_id

        **为什么要造一只**：真实开发盒的 schema 把 set_id 声明成必填，
        所以「对象里缺 set_id」会先被**结构契约闸**拦下——
        trigger 里的 subfield 检查根本没机会触发。
        那条守卫就永远不承重（拒的原因不是它）。

        造这只盒子，是为了让「对象里缺这个键」只有 trigger 那一处能拦：
        契约放行了（它不知道 subfield 的存在），只剩 subfield 检查。
        """
        import kanban.trigger as trigger_mod

        definition = BOX_REGISTRY["wms_development_box"][0]()
        schema = definition.get_schema("schema_dev_task_input")
        lax_fields = [
            f if f.name != "design_package_ref" else FieldDef(
                name="design_package_ref", type="object", required=True,
                properties=[FieldDef(name="package_id", type="string")],
            )
            for f in schema.fields
        ]
        lax_schema = schema.model_copy(update={"fields": lax_fields})
        lax_schemas = [
            lax_schema if s.id == schema.id else s for s in definition.schemas
        ]
        lax = definition.model_copy(update={"schemas": lax_schemas})

        monkeypatch.setitem(
            trigger_mod.BOX_REGISTRY, "wms_development_box",
            (lambda: lax, BOX_REGISTRY["wms_development_box"][1]),
        )
        return lax

    def test_the_subfield_must_exist_in_the_object(self, lax_dev_box):
        """对象里没这个键 → 拒绝，不静默降级成空

        这条只可能由 trigger 的 subfield 检查拦下——
        契约闸不知道 subfield 的存在（见 lax_dev_box 的说明）。
        """
        schema = lax_dev_box.get_schema("schema_dev_task_input")
        payload = _minimal_payload(schema, lax_dev_box.schemas)
        payload["design_package_ref"] = {"package_id": "p1"}  # 缺 set_id
        with pytest.raises(TriggerError) as exc:
            trigger_task(
                "wms_development_box", "handle_development_request",
                payload, TaskRunStore("/tmp/_sr_sub.jsonl"),
            )
        assert "set_id" in str(exc.value), (
            f"报错没点名缺的是哪个键：{exc.value}"
        )

    def test_the_subfield_value_must_be_a_string(self, lax_dev_box):
        """子字段的值必须是 id 字符串——不是又一份对象"""
        schema = lax_dev_box.get_schema("schema_dev_task_input")
        payload = _minimal_payload(schema, lax_dev_box.schemas)
        payload["design_package_ref"] = {
            "package_id": "p1",
            "set_id": {"nested": "object"},
        }
        with pytest.raises(TriggerError) as exc:
            trigger_task(
                "wms_development_box", "handle_development_request",
                payload, TaskRunStore("/tmp/_sr_sub3.jsonl"),
            )
        assert "set_id" in str(exc.value) or "id string" in str(exc.value)

    def test_a_declared_subfield_requires_an_object(self):
        """声明了 subfield，值却不是对象 → 拒绝

        那是「该给整份对象却给了个 id」——或者反过来，是投料形状不对。
        两种都不该悄悄放过。

        拦在哪一层不重要：结构契约（`ref:` 深校验）会先拦下
        「这不是个对象」，trigger 里的 subfield 检查是第二道。
        断言的是**结果**（被拒 + 报错点名了哪个字段）。
        """
        schema = _schema_of("wms_development_box", "handle_development_request")
        payload = _minimal_payload(schema, _schemas_of("wms_development_box"))
        payload["design_package_ref"] = "REQ-2026-003"  # 是 id，不是对象
        with pytest.raises(TriggerError) as exc:
            trigger_task(
                "wms_development_box", "handle_development_request",
                payload, TaskRunStore("/tmp/_sr_sub2.jsonl"),
            )
        assert "design_package_ref" in str(exc.value), (
            f"报错没点名是哪个字段的形状不对：{exc.value}"
        )

    def test_the_id_is_taken_from_the_object(self, tmp_path):
        """真的取到了——取到的是对象里那个 id，不是整个对象"""
        store = TaskRunStore(tmp_path / "runs.jsonl")
        schema = _schema_of("wms_development_box", "handle_development_request")
        payload = _minimal_payload(schema, _schemas_of("wms_development_box"))
        payload["design_package_ref"] = {
            "package_id": "p1",
            "set_id": "REQ-2026-007",
            "model_elements": {},
        }
        result = trigger_task(
            "wms_development_box", "handle_development_request", payload, store,
        )
        run = store.get_run(result["task_run_id"])
        assert run["subject_ref"] == "REQ-2026-007", (
            f"取到的标识是 {run['subject_ref']!r}，应该是对象里的 set_id"
        )


# ============================================================
# 业务对象的种类（subject_kind）
# ============================================================


class TestTheKindIsDeclared:
    """「它关于哪一类业务对象」要显式声明，不许运行时猜"""

    def test_declaring_the_field_without_the_kind_is_rejected(self, monkeypatch):
        """声明了 subject_ref_field 却没声明 subject_kind → 拒绝这次履约

        不声明就等于让运行时去猜「这是需求还是运维事件」。
        猜错了**没人会发现**——归档里两类记录混在一起，
        看板上还是「一切正常」。
        """
        import kanban.trigger as trigger_mod

        definition = BOX_REGISTRY["wms_design_box"][0]()
        broken_task = definition.task[0].model_copy(update={"subject_kind": None})
        broken = definition.model_copy(update={"task": [broken_task]})

        monkeypatch.setitem(
            trigger_mod.BOX_REGISTRY, "wms_design_box",
            (lambda: broken, BOX_REGISTRY["wms_design_box"][1]),
        )
        schema = broken.get_schema(broken.task[0].task_schema)
        with pytest.raises(TriggerError) as exc:
            trigger_task(
                "wms_design_box", broken.task[0].type,
                _minimal_payload(schema, _schemas_of("wms_design_box")) | {"set_id": "REQ-1"},
                TaskRunStore("/tmp/_sr_kind.jsonl"),
            )
        assert "subject_kind" in str(exc.value)

    def test_the_kind_lands_in_the_record(self, tmp_path):
        store = TaskRunStore(tmp_path / "runs.jsonl")
        schema = _schema_of("wms_design_box", "handle_modeling_request")
        result = trigger_task(
            "wms_design_box", "handle_modeling_request",
            _minimal_payload(schema, _schemas_of("wms_design_box")) | {"set_id": "REQ-1"}, store,
        )
        run = store.get_run(result["task_run_id"])
        assert run["subject_kind"] == "requirement", (
            f"记录里的 subject_kind 是 {run['subject_kind']!r}"
        )

    def test_the_ops_box_declares_its_own_kind(self):
        """运维盒的 subject 是**运维事件**，不是需求

        需求号和运维事件号可能撞号。不区分种类，归档里就分不出
        「这条需求的一次运维」和「一个运维事件」。
        """
        ops = BOX_REGISTRY["wms_ops_box"][0]()
        kinds = {t.subject_kind for t in ops.task if t.subject_ref_field}
        assert kinds == {"ops_event"}, (
            f"运维盒声明的种类是 {kinds}，应该是 ops_event"
        )

    def test_same_ref_different_kinds_can_be_told_apart(self, tmp_path):
        store = TaskRunStore(tmp_path / "runs.jsonl")
        schema = _schema_of("wms_design_box", "handle_modeling_request")
        trigger_task(
            "wms_design_box", "handle_modeling_request",
            _minimal_payload(schema, _schemas_of("wms_design_box")) | {"set_id": "OPS-1"}, store,
        )
        assert len(store.list_by_subject("OPS-1")) == 1
        assert len(store.list_by_subject("OPS-1", subject_kind="requirement")) == 1
        assert len(store.list_by_subject("OPS-1", subject_kind="ops_event")) == 0


# ============================================================
# 辅助
# ============================================================


def _schemas_of(box_id: str):
    """这只盒子声明的全部 schema（用来解析 ref:）"""
    return BOX_REGISTRY[box_id][0]().schemas


def _schema_of(box_id: str, task_type: str):
    definition = BOX_REGISTRY[box_id][0]()
    task = next(t for t in definition.task if t.type == task_type)
    return definition.get_schema(task.task_schema)


def _resolve_schema(schema_id: str, schemas) -> object | None:
    """按 id 找 schema（schemas 是 list 或 dict 都行）"""
    if isinstance(schemas, dict):
        found = schemas.get(schema_id)
        return found if found is not None else None
    for s in schemas or []:
        if getattr(s, "id", None) == schema_id:
            return s
    return None


def _minimal_payload(schema, schemas=None) -> dict:
    """按 schema 造一份最小合法投料（required 字段都给标量占位）

    **ref: 字段要递归造一份合法对象**：
    以前一律给字符串 "x"，于是契约校验先报「expected object, got str」，
    把「缺 subject_ref」这条**真正要测的**错误盖住了——
    守卫看到的是另一个字段的名字，测的就不是它要测的东西。
    """
    payload: dict = {}
    for f in schema.fields:
        if not f.required:
            continue
        if f.type.startswith("ref:"):
            target = _resolve_schema(f.type[len("ref:"):], schemas)
            payload[f.name] = _minimal_payload(target, schemas) if target else {}
        elif f.type == "array":
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
