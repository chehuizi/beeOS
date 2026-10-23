"""tests/test_box_refs.py - ref: 引用要真的指向本盒的 schema，且形状真的被校验

**背景（这是文档写了、代码从来没实现的一条规则）**

`docs/beebox-definition-schema.md` §4 白纸黑字：
「schema 内 `ref:` 引用必须指向本 definition schemas 块内真实存在的 schema id」。

实测**有 3 处悬空**：
- `wms_development` 的 `design_package_ref` → `schema_business_model_package`（在建模盒里）
- `wms_testing` 的 `artifact_ref` → `schema_build_artifact`（在开发盒里）
- `wms_testing` 的 `requirement_ref` → `schema_requirement_set_package`（在需求盒里）

悬空的后果**不是报错，是没人校验**：那个 id 在本盒里根本不存在，
于是「制品该长什么样」「需求集该长什么样」从来没被定义过，
投料里塞什么都行，产出照样往下游走。

它跟之前那 4 条永不触发的 exception 是同一族病：**声明了约束，但没人执行**。

这组守卫盯四件事：
1. 每处 `ref:` 都能在本盒 schemas 里解析到（**这条在修之前是红的**）
2. `ref:` 字段真的被深校验——形状不对要报错，不是放行
3. ref 解析不到就报错（不留「校验不了就当过了」的口子）
4. 盒间接力通道按**目标盒自己声明的形状**校验上游产出
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pytest

from core.models import FieldDef, SchemaDef
from kanban.trigger import BOX_REGISTRY, TriggerError, trigger_task
from runtime.contract import validate_payload
from runtime.handoff import HandoffError, assemble_handoff
from runtime.store import TaskRunStore


# ============================================================
# 辅助
# ============================================================


def _iter_fields(fields) -> Iterator[FieldDef]:
    """递归遍历 schema 里所有字段（含 object 的 properties 和 array 的 items）"""
    for f in fields or []:
        yield f
        yield from _iter_fields(f.properties)
        if f.items is not None:
            yield from _iter_fields([f.items])


def _ref_ids(schema: SchemaDef) -> list[tuple[str, str]]:
    """schema 里所有 (字段路径, ref 目标 id)"""
    out = []
    for f in _iter_fields(schema.fields):
        if f.type.startswith("ref:"):
            out.append((f.name, f.type[len("ref:"):]))
    return out


def _schema_of(box_id: str, task_type: str) -> SchemaDef:
    definition = BOX_REGISTRY[box_id][0]()
    task = next(t for t in definition.task if t.type == task_type)
    return definition.get_schema(task.task_schema)


# ============================================================
# 1. 没有悬空引用
# ============================================================


class TestNoDanglingRefs:
    """每处 ref: 都必须能在本盒 schemas 里解析到"""

    def test_every_ref_resolves_within_the_same_box(self):
        dangling = []
        for bid, (loader, _) in BOX_REGISTRY.items():
            definition = loader()
            own = {s.id for s in definition.schemas}
            for schema in definition.schemas:
                for field_name, target in _ref_ids(schema):
                    if target not in own:
                        dangling.append(f"{bid}: {schema.id}.{field_name} → {target}")
        assert not dangling, (
            "ref: 指向了本盒不存在的 schema id——"
            "文档（docs/beebox-definition-schema.md §4）要求 ref 只引用本 "
            "definition 内。悬空引用的后果不是报错，是**没人校验**："
            f"\n  " + "\n  ".join(dangling)
        )

    def test_the_rule_actually_covers_refs(self):
        """守卫本身得真的看得见 ref——不然它是摆设

        断言的是「至少有 N 处 ref 被检查到」，不是「某个具体盒子」。
        盒子会变、ref 会挪位置；「有 ref 且它们都解析得到」是不变量。
        """
        total = 0
        for _bid, (loader, _) in BOX_REGISTRY.items():
            for schema in loader().schemas:
                total += len(_ref_ids(schema))
        assert total >= 6, (
            f"只找到 {total} 处 ref——本仓的 ref 至少 6 处。"
            f"数量掉到这个数，说明 schema 结构变了，这条守卫可能已经空转"
        )

    def test_task_and_result_schema_point_inside_the_box(self):
        """task_schema / result_schema 也必须在本盒 schemas 里"""
        for bid, (loader, _) in BOX_REGISTRY.items():
            definition = loader()
            own = {s.id for s in definition.schemas}
            for task in definition.task:
                assert task.task_schema in own, (
                    f"{bid}/{task.type}: task_schema {task.task_schema!r} 不在本盒"
                )
            assert definition.result.result_schema in own, (
                f"{bid}: result_schema {definition.result.result_schema!r} 不在本盒"
            )


# ============================================================
# 2. ref: 真的被深校验
# ============================================================


class TestDeepValidation:
    """ref: 字段的值必须真的符合被引用 schema 的形状"""

    def test_a_wrong_shaped_ref_value_is_rejected(self):
        definition = BOX_REGISTRY["wms_development_box"][0]()
        schema = definition.get_schema("schema_dev_task_input")
        # package_id 必须是 string，这里给了整数
        bad = {
            "dev_task_id": "d1",
            "design_package_ref": {
                "package_id": 123,          # ← 形状不对
                "set_id": "REQ-1",
                "model_elements": {},
            },
            "commit_range": "a..b",
            "target_env": "dev",
        }
        errors = validate_payload(bad, schema, schemas=definition.schemas)
        assert errors, "形状不对的 ref 值居然通过了——ref 仍是空校验"
        assert any("package_id" in e for e in errors), (
            f"报错没点名是哪个字段错了：{errors}"
        )

    def test_a_missing_required_field_inside_a_ref_is_rejected(self):
        definition = BOX_REGISTRY["wms_development_box"][0]()
        schema = definition.get_schema("schema_dev_task_input")
        bad = {
            "dev_task_id": "d1",
            "design_package_ref": {"package_id": "p1"},  # ← 缺 set_id / model_elements
            "commit_range": "a..b",
            "target_env": "dev",
        }
        errors = validate_payload(bad, schema, schemas=definition.schemas)
        assert errors, "ref 里缺必填字段居然通过了"
        assert any("set_id" in e for e in errors), f"报错没点名缺哪个字段：{errors}"

    def test_a_correctly_shaped_ref_value_passes(self):
        definition = BOX_REGISTRY["wms_development_box"][0]()
        schema = definition.get_schema("schema_dev_task_input")
        good = {
            "dev_task_id": "d1",
            "design_package_ref": {
                "package_id": "p1",
                "set_id": "REQ-1",
                "model_elements": {
                    "entities": [{"entity_id": "e1", "name": "SKU", "trace_to": "r1"}],
                    "specifications": [],
                    "domain_services": [],
                    "domain_events": [],
                    "domain_metrics": [],
                },
            },
            "commit_range": "a..b",
            "target_env": "dev",
        }
        assert validate_payload(good, schema, schemas=definition.schemas) == []

    def test_an_unresolvable_ref_is_an_error_not_a_pass(self):
        """ref 解析不到 → 报错，不放行

        「校验不了」不能等于「校验通过」。以前那句注释
        「`ref:<schema_id>` 等暂不做深校验，放行」就是这条。
        """
        schema = SchemaDef(
            id="schema_probe",
            fields=[FieldDef(name="thing", type="ref:schema_missing", required=True)],
        )
        errors = validate_payload({"thing": {"a": 1}}, schema)
        assert errors, "ref 解析不到居然放行了——那是「没有约束」不是「约束通过」"
        assert any("schema_missing" in e for e in errors), (
            f"报错没点名是哪个 ref 解析不了：{errors}"
        )

    def test_a_ref_to_a_schema_in_another_box_is_an_error(self):
        """把别的盒子的 schema id 写进来 → 解析不到 → 报错

        这正是那 3 处悬空引用的形态。它们以前是「静默通过」，
        现在会当场被拒——除非引用被改成**本盒自己声明的形状**。
        """
        dev_definition = BOX_REGISTRY["wms_development_box"][0]()
        modeling_definition = BOX_REGISTRY["wms_design_box"][0]()
        schema = SchemaDef(
            id="schema_probe",
            fields=[
                FieldDef(name="thing", type="ref:schema_business_model_package",
                         required=True)
            ],
        )
        errors = validate_payload(
            {"thing": {"package_id": "p"}},
            schema,
            schemas=dev_definition.schemas,  # 开发盒的 schemas 里没有它
        )
        assert errors, "引用别的盒子的 schema id 居然解析成功了"

        # 放进**正确**的盒子里就解析得到——证明拦的是「悬空」，不是「ref 本身」
        ok = validate_payload(
            {"thing": {"package_id": "p"}},
            schema,
            schemas=modeling_definition.schemas,
        )
        assert not any("无法解析" in e for e in ok), (
            f"在建模盒的 schemas 里 ref 应该解析得到：{ok}"
        )


# ============================================================
# 3. 盒间接力通道
# ============================================================


class TestHandoff:
    """handoff 把上游产出接到下游投料的 ref: 字段上"""

    def test_a_ref_field_gets_the_upstream_artifact(self):
        definition = BOX_REGISTRY["wms_development_box"][0]()
        schema = definition.get_schema("schema_dev_task_input")
        artifact = {
            "package_id": "p1",
            "set_id": "REQ-1",
            "model_elements": {
                "entities": [], "specifications": [], "domain_services": [],
                "domain_events": [], "domain_metrics": [],
            },
        }
        payload = assemble_handoff(
            {"dev_task_id": "d1", "commit_range": "a..b", "target_env": "dev"},
            {"design_package_ref": "run-1"},
            task_schema=schema,
            schemas=definition.schemas,
            get_run=lambda rid: {"status": "completed", "artifact": artifact}
            if rid == "run-1" else None,
        )
        assert payload["design_package_ref"] == artifact

    def test_a_missing_upstream_run_is_rejected(self):
        definition = BOX_REGISTRY["wms_development_box"][0]()
        schema = definition.get_schema("schema_dev_task_input")
        with pytest.raises(HandoffError):
            assemble_handoff(
                {"dev_task_id": "d1", "commit_range": "a..b", "target_env": "dev"},
                {"design_package_ref": "run-nope"},
                task_schema=schema,
                schemas=definition.schemas,
                get_run=lambda rid: None,
            )

    def test_a_non_ref_field_cannot_be_handed_off(self):
        """handoff 只能填 ref: 字段

        普通字段自己传。让交接通道去接普通字段，就是让它猜
        「这个字段该接上游的哪一块」——那是业务约定，猜不得。
        """
        definition = BOX_REGISTRY["wms_development_box"][0]()
        schema = definition.get_schema("schema_dev_task_input")
        with pytest.raises(HandoffError) as exc:
            assemble_handoff(
                {"dev_task_id": "d1", "commit_range": "a..b", "target_env": "dev"},
                {"dev_task_id": "run-1"},
                task_schema=schema,
                schemas=definition.schemas,
                get_run=lambda rid: {"status": "completed", "artifact": {"a": 1}},
            )
        assert "ref:" in str(exc.value)

    def test_an_unknown_field_is_rejected(self):
        definition = BOX_REGISTRY["wms_development_box"][0]()
        schema = definition.get_schema("schema_dev_task_input")
        with pytest.raises(HandoffError) as exc:
            assemble_handoff(
                {"dev_task_id": "d1", "commit_range": "a..b", "target_env": "dev"},
                {"no_such_field": "run-1"},
                task_schema=schema,
                schemas=definition.schemas,
                get_run=lambda rid: {"status": "completed", "artifact": {"a": 1}},
            )
        assert "no_such_field" in str(exc.value)

    def test_a_wrong_shaped_upstream_artifact_is_rejected(self):
        """上游产出的形状不对下游的声明 → 拒，不放行

        这是「接过来的根本不是那回事」的唯一防线。
        """
        definition = BOX_REGISTRY["wms_development_box"][0]()
        schema = definition.get_schema("schema_dev_task_input")
        with pytest.raises(HandoffError) as exc:
            assemble_handoff(
                {"dev_task_id": "d1", "commit_range": "a..b", "target_env": "dev"},
                {"design_package_ref": "run-1"},
                task_schema=schema,
                schemas=definition.schemas,
                get_run=lambda rid: {
                    "status": "completed",
                    "artifact": {"totally": "something else"},
                },
            )
        assert "package_id" in str(exc.value) or "set_id" in str(exc.value)

    def test_a_failed_upstream_run_cannot_be_handed_off(self):
        definition = BOX_REGISTRY["wms_development_box"][0]()
        schema = definition.get_schema("schema_dev_task_input")
        with pytest.raises(HandoffError) as exc:
            assemble_handoff(
                {"dev_task_id": "d1", "commit_range": "a..b", "target_env": "dev"},
                {"design_package_ref": "run-1"},
                task_schema=schema,
                schemas=definition.schemas,
                get_run=lambda rid: {"status": "failed", "artifact": {"package_id": "p"}},
            )
        assert "失败" in str(exc.value)

    def test_a_manual_value_and_a_handoff_together_are_rejected(self):
        """一个字段两个来源 → 拒

        不拒的话，接过来的那份会**悄悄覆盖**手填的那份，
        而调用方以为自己传的那份生效了。

        这里的制品是**合法**的——否则形状校验会先拒掉，
        那这条守卫就永远不承重（拒的原因不是它）。
        """
        definition = BOX_REGISTRY["wms_development_box"][0]()
        schema = definition.get_schema("schema_dev_task_input")
        with pytest.raises(HandoffError) as exc:
            assemble_handoff(
                {
                    "dev_task_id": "d1",
                    "commit_range": "a..b",
                    "target_env": "dev",
                    "design_package_ref": {
                        "package_id": "p1", "set_id": "s", "model_elements": {},
                    },
                },
                {"design_package_ref": "run-1"},
                task_schema=schema,
                schemas=definition.schemas,
                get_run=lambda rid: {
                    "status": "completed",
                    "artifact": {
                        "package_id": "p2", "set_id": "t", "model_elements": {},
                    },
                },
            )
        assert "design_package_ref" in str(exc.value)


# ============================================================
# 4. 一条需求沿链路走完
# ============================================================


class TestOneRequirementWalksTheChain:
    """需求 → 设计 → 开发：同一条需求，归档里是同一个 subject_ref

    这是「1 个需求沿 requirement box 完成交付」的**最小机制证明**：
    三只盒子接力，投料靠 handoff 传，归档靠 subject_ref 串起来。

    这里只走到开发盒——因为开发盒的腿还没实现，它产出的不是制品，
    接给测试盒会被形状校验拒掉（那是对的，见下面的用例）。
    """

    @pytest.fixture(autouse=True)
    def stub_llm(self, monkeypatch):
        from runtime import llm
        from tests.conftest import stub_llm_dispatch

        monkeypatch.setattr(llm, "complete_json", stub_llm_dispatch)

    def test_three_boxes_one_subject_ref(self, tmp_path):
        store = TaskRunStore(Path(tmp_path) / "runs.jsonl")

        # 1) 需求盒：自然语言 → 需求集
        req = trigger_task(
            "wms_requirement_box", "capture_business_requirement",
            {"narrative": "仓库管理库存批次。同一 SKU 不能重复占用库位。库存不能为负。"},
            store,
        )
        req_run = store.get_run(req["task_run_id"])
        set_id = req_run["artifact"]["set_id"]
        assert set_id, "需求盒的产出里没有 set_id"

        # 2) 设计盒：需求集 → 设计包
        design_payload = {
            "set_id": set_id,
            "business_goal": req_run["artifact"].get("business_goal", ""),
            "context": "",
            "requirements": [
                {
                    "requirement_id": r.get("requirement_id", ""),
                    "description": r.get("description", ""),
                    "requirement_type": r.get("requirement_type", "object"),
                    "priority": r.get("priority", "must_have"),
                }
                for r in req_run["artifact"].get("requirements", [])
            ],
        }
        design = trigger_task(
            "wms_design_box", "handle_modeling_request", design_payload, store,
        )
        design_run = store.get_run(design["task_run_id"])
        assert design_run["subject_ref"] == set_id, (
            f"设计盒的 subject_ref 是 {design_run['subject_ref']!r}，"
            f"应该是需求集的 set_id {set_id!r}"
        )

        # 3) 开发盒：设计包靠 handoff 接进来
        dev = trigger_task(
            "wms_development_box", "handle_development_request",
            {"dev_task_id": "d1", "commit_range": "a..b", "target_env": "dev"},
            store,
            handoff={"design_package_ref": design["task_run_id"]},
        )
        dev_run = store.get_run(dev["task_run_id"])
        assert dev_run["subject_ref"] == set_id, (
            f"开发盒的 subject_ref 是 {dev_run['subject_ref']!r}，"
            f"应该隔着设计包取到同一个 set_id {set_id!r}"
        )
        assert dev_run["subject_kind"] == "requirement"

        # 三只盒子里，设计和开发认的是同一条需求
        assert design_run["subject_kind"] == dev_run["subject_kind"] == "requirement"
        assert len(store.list_by_subject(set_id, subject_kind="requirement")) == 2

    def test_the_development_box_cannot_yet_hand_off_to_testing(self, tmp_path):
        """开发盒的腿还没实现 → 它的产出不是制品 → 接给测试盒要被拒

        这条断言的是**拒得对**：开发盒现在交出的是
        `{package_id, box_id, maturity, evidence}` 这种占位形状，
        测试盒声明的制品形状是另一套。交接通道必须当场拒，
        而不是把一份不像制品的东西接到测试盒去。
        """
        store = TaskRunStore(Path(tmp_path) / "runs.jsonl")
        design = trigger_task(
            "wms_design_box", "handle_modeling_request",
            {
                "set_id": "REQ-CHAIN",
                "business_goal": "g",
                "context": "",
                "requirements": [
                    {"requirement_id": "r1", "description": "SKU",
                     "requirement_type": "object", "priority": "must_have"},
                ],
            },
            store,
        )
        dev = trigger_task(
            "wms_development_box", "handle_development_request",
            {"dev_task_id": "d1", "commit_range": "a..b", "target_env": "dev"},
            store,
            handoff={"design_package_ref": design["task_run_id"]},
        )
        with pytest.raises(TriggerError) as exc:
            trigger_task(
                "wms_testing_box", "handle_testing_request",
                {"test_task_id": "t1", "scenarios": ["收货上架"]},
                store,
                handoff={"artifact_ref": dev["task_run_id"]},
            )
        assert "artifact_ref" in str(exc.value)
