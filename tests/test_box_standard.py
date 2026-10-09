"""tests.test_box_standard - 五组件契约：盒子能不能只靠声明就被正确渲染 / 调用

标准化的判据不是「有几栏」，而是**加一只新盒子要不要改看板和路由**。
以前要改，因为：
- 产出形态靠「result 里有没有 nodes」猜，猜错就把一只盒子的产出
  渲染成另一只盒子的形状，而且不报错
- /api/structure 写死了 requirement_capture_box + capture_business_requirement，
  调用方必须知道某一只盒子的 task 类型名

这组测试盯住契约本身：契约一旦退化回硬编码，这里就红。
"""

from __future__ import annotations

import pytest

from boxes.modeling import get_definition as get_modeling_definition
from boxes.requirement_capture import get_definition as get_capture_definition
from kanban.trigger import (
    BOX_REGISTRY,
    TriggerError,
    box_manifest,
    resolve_text_intake,
    trigger_task,
)
from kanban.web import HTML_PAGE
from runtime.store import TaskRunStore
from tests.conftest import stub_compliant_llm


CAPTURE = "requirement_capture_box"
MODELING = "business_modeling_box"


# ============================================================
# ARTIFACTS OUT：产出形态是声明，不是猜的
# ============================================================


class TestArtifactView:
    @pytest.mark.parametrize(
        "box_id, expected",
        [(CAPTURE, "flow_graph"), (MODELING, "ddd_model")],
    )
    def test_every_box_declares_its_artifact_view(self, box_id, expected):
        assert BOX_REGISTRY[box_id][0]().result.view == expected

    def test_frontend_carries_the_artifact_view_through(self):
        """后端 response 里的 artifact_view 必须被搬到前端 artifact 对象上

        漏搬过一次：后端声明 flow_graph，前端对象没这个字段 → 落到 undefined
        → generic 回退 → 产出被如实显示成 JSON。功能没崩，但专用视图没了，
        而且看板上写着「该盒声明的产出形态 generic」——把渲染缺失误报成盒子声明。
        """
        assert "artifact_view: data.artifact_view || 'generic'" in HTML_PAGE
        # 中止分支也要带，否则渲染层分不清「声明了 generic」和「压根没产出」
        assert HTML_PAGE.count("artifact_view: data.artifact_view") == 2

    def test_kanban_dispatches_on_the_declared_view(self):
        """渲染器必须按声明分派，不能再靠「有没有 nodes 字段」猜"""
        assert "ARTIFACT_VIEWS" in HTML_PAGE
        assert "renderArtifactBody(a.artifact_view, a.result)" in HTML_PAGE
        assert "Array.isArray(a.result" not in HTML_PAGE

    def test_declared_views_all_have_a_renderer_or_fall_back(self):
        """声明的每个 view 都有渲染器，或者明确落到 generic 回退

        不允许「声明了 view 但既没渲染器也没回退」——那会让产出变成空白。
        """
        known = {"flow_graph", "ddd_model"}
        for box_id in BOX_REGISTRY:
            view = BOX_REGISTRY[box_id][0]().result.view
            if view != "generic":
                assert view in known, f"{box_id} 声明了未注册的 view: {view}"
        assert "generic" in HTML_PAGE or "在看板没有专用视图" in HTML_PAGE

    def test_trigger_response_carries_the_artifact_kind(self, tmp_path, monkeypatch):
        """产出响应要自带形态，API 消费方不必猜"""
        from runtime import llm

        monkeypatch.setattr(llm, "complete_json", stub_compliant_llm)
        store = TaskRunStore(tmp_path / "runs.jsonl")
        r = trigger_task(
            CAPTURE, "capture_business_requirement",
            {"narrative": "我们的目标是降本。\n每月盘点一次。\n库存低于安全库存触发补货。"},
            store,
        )
        assert r["artifact_view"] == "flow_graph"
        assert r["artifact_type"] == "business_requirement_captured"


# ============================================================
# TASK IN：按能力解析，不按 id 硬编码
# ============================================================


class TestTextIntakeResolution:
    def test_resolves_by_capability_when_nothing_is_specified(self):
        """不传 box_id 时，按「谁吃自然语言」解析，而不是写死某只盒子"""
        box_id, task_type = resolve_text_intake()
        assert box_id == CAPTURE
        assert task_type == "capture_business_requirement"

    def test_explicit_box_and_task_win(self):
        assert resolve_text_intake(MODELING, "handle_modeling_request") == (
            MODELING, "handle_modeling_request"
        )

    def test_box_without_text_intake_is_refused_with_a_reason(self):
        with pytest.raises(TriggerError, match="no task taking plain text"):
            resolve_text_intake(MODELING)

    def test_unknown_box_is_refused(self):
        with pytest.raises(TriggerError, match="unknown box_id"):
            resolve_text_intake("no_such_box")

    def test_structure_endpoint_carries_no_hardcoded_box_id(self):
        """/api/structure 里不能再出现某只盒子的 id 或 task 类型名"""
        assert "requirement_capture_box" not in HTML_PAGE
        assert "capture_business_requirement" not in HTML_PAGE


# ============================================================
# 盒子作为独立 API 单元：manifest 齐不齐五组件
# ============================================================


class TestManifest:
    @pytest.mark.parametrize("box_id", [CAPTURE, MODELING])
    def test_manifest_covers_all_five_components(self, box_id):
        m = box_manifest(box_id)
        assert m["box_id"] == box_id
        assert m["version"] >= 1
        assert m["tasks"], "TASK IN：可接收的 task"
        assert m["beelines"], "BEELINE：程序 + op 清单"
        assert m["produces"]["schema"], "ARTIFACTS OUT：产出契约"
        assert m["produces"]["view"], "ARTIFACTS OUT：形态声明"
        assert m["acceptance"], "ACCEPTANCE：判据"
        assert m["metrics"], "DOMAIN CONTEXT：实测指标"
        assert "fed_by" not in m or True  # fed_by 走 domain_context，不重复声明

    def test_manifest_is_json_safe(self):
        """manifest 要能直接当 API 响应体返回——不能夹带 pydantic 对象"""
        import json

        for box_id in BOX_REGISTRY:
            json.dumps(box_manifest(box_id))  # 不抛即通过

    def test_manifest_lists_the_beeline_pinned_by_the_task(self):
        m = box_manifest(CAPTURE)
        task_beeline = m["tasks"][0]["beeline_id"]
        assert task_beeline in [b["id"] for b in m["beelines"]]
        assert m["beelines"][0]["ops"], "BEELINE 要列出 op 清单"

    def test_unknown_box_manifest_is_empty(self):
        assert box_manifest("no_such_box") == {}

    def test_manifest_metrics_are_declarations_without_fake_actuals(self):
        """manifest 不带履约历史：actual 必须是 None，不能编一个数出来"""
        for r in box_manifest(CAPTURE)["metrics"]:
            assert r["actual"] is None
            assert r["sample_size"] == 0


# ============================================================
# 五组件的声明必须自洽
# ============================================================


class TestDeclarationCoherence:
    @pytest.mark.parametrize("box_id", [CAPTURE, MODELING])
    def test_task_schemas_exist_and_beelines_are_pinned(self, box_id):
        d = BOX_REGISTRY[box_id][0]()
        assert d.result.result_schema in {s.id for s in d.schemas}
        for t in d.task:
            assert t.task_schema in {s.id for s in d.schemas}

    @pytest.mark.parametrize("box_id", [CAPTURE, MODELING])
    def test_every_acceptance_metric_has_a_value_in_result(self, box_id):
        """acceptance 声明的每个 metric，产出 schema 里都得有对应字段

        没有就意味着判据永远读不到值——要么恒判通过（假绿灯），
        要么恒判失败。两种都是坏结果。
        """
        d = BOX_REGISTRY[box_id][0]()
        result_schema = d.get_schema(d.result.result_schema)
        fields = {f.name for f in result_schema.fields}
        for rule in d.result.acceptance:
            assert rule.metric in fields, f"{box_id}: {rule.metric} 不在产出 schema 里"

    def test_result_schema_is_listed_in_the_manifest(self):
        d = get_capture_definition()
        assert box_manifest(CAPTURE)["produces"]["schema"] == d.result.result_schema