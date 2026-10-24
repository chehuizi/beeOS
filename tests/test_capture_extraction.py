"""tests.test_capture_extraction - 需求盒抽取层的守卫

这一组守的是 **抽取层的三条契约**（每一组都有能独立触发的失败）：

1. **一句话的真需求抽得出东西**
   （旧契约：首句 = goal 不抽节点 → 单句需求产出 0 条 → 链在第 1 只盒子就断）

2. **覆盖率的分母是内容句（goal + fact），不是「除首句外的句子」**
   （旧契约：首句不进分母 → 「需求写在第一句」既不算覆盖也不算漏 → 需求消失）

3. **约束挂得住**
   （旧契约：约束只挂边 → 整张图只有一个节点时无处可挂 → 约束整个丢掉）

另外两条边界：
- **[title] 是名字不是需求**，不进分母（进分母就是逼模型编碎片）
- **「业务方什么也没说」和「模型没干活」不许混成一件**
"""

from __future__ import annotations

import inspect
import tempfile
from pathlib import Path

import pytest

from boxes.requirement_capture import runner as capture_runner
from boxes.requirement_capture.runner import (
    _attach_sources,
    _content_sentences,
    _split_sentences,
)
from kanban.trigger import trigger_task
from runtime.store import TaskRunStore


@pytest.fixture
def store() -> TaskRunStore:
    return TaskRunStore(Path(tempfile.mktemp(suffix=".jsonl")))


def _run_capture(store, narrative, llm_result=None):
    if llm_result is None:
        return trigger_task(
            "wms_requirement_box", "capture_business_requirement",
            {"narrative": narrative}, store,
        )
    # **桩用上下文装、用完就还原**——直接赋值 llm.complete_json
    # 会漏到下一个测试文件去：那边以为自己调的是真 LLM，
    # 其实调的是上一个文件留下的桩，失败原因离现场隔着两个文件。
    from runtime import llm
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(llm, "complete_json", lambda *a, **kw: llm_result)
        return trigger_task(
            "wms_requirement_box", "capture_business_requirement",
            {"narrative": narrative}, store,
        )


# ============================================================
# 一句话的真需求
# ============================================================


class TestSingleSentenceRequirement:
    def test_一句话的需求抽得出需求集(self, store):
        """业务方只写一句话（最常见的写法）→ 必须产出需求

        旧契约下这里是 0 条需求 + source_coverage=0 → 判红 → 链断。
        """
        r = _run_capture(
            store, "同一 SKU 出库必须先出快过期的批次",
            llm_result={"nodes": [
                {"action": "同一 SKU 出库必须先出快过期的批次",
                 "trace_to": "sent_1"},
            ]},
        )
        assert r["status"] == "completed"
        assert r["acceptance_status"] == "accepted"
        assert r["result"]["requirement_count"] >= 1
        assert r["result"]["source_coverage"] == 100.0

    def test_单句时只有一条内容句_分母是_1(self, store):
        """分母 = 内容句 = 1（不是 0）

        旧契约：fact 句数 = 0 → coverage = 0/0 → 判红。
        「0/0 判成 0%」和「真的没覆盖」是两件事。
        """
        s = _split_sentences("同一 SKU 出库必须先出快过期的批次")
        assert len(_content_sentences(s)) == 1

    def test_两句话时首句也在分母里(self, store):
        """「同一 SKU 出库必须先出快过期的批次；临期 30 天内 SKU 要能在库存看板看到」

        首句才是那句真正的需求（旧契约把它当目标丢掉）。
        """
        s = _split_sentences(
            "同一 SKU 出库必须先出快过期的批次；临期 30 天内 SKU 要能在库存看板看到")
        content = _content_sentences(s)
        assert len(content) == 2
        assert content[0]["role"] == "goal"


# ============================================================
# 覆盖率的分母
# ============================================================


class TestCoverageDenominator:
    def test_goal_句没被覆盖时_coverage_掉下来(self, store):
        r = _run_capture(
            store, "建模退款流程。订单是核心实体。",
            llm_result={"nodes": [
                {"action": "订单是核心实体", "trace_to": "sent_2"},
            ]},
        )
        res = r["result"]
        assert res["source_coverage"] == 50.0
        assert res["evidence_detail"]["uncovered_sentences"] == ["sent_1"]
        assert r["acceptance_status"] == "rejected"

    def test_title_句不进分母(self, store):
        """「wms的流程」是名字，没被任何节点指向，但不该算漏"""
        r = _run_capture(
            store, "库存管理有入库流程、出库流程",
            llm_result={"nodes": [
                {"action": "入库流程", "trace_to": "sent_2"},
                {"action": "出库流程", "trace_to": "sent_3"},
            ]},
        )
        res = r["result"]
        assert res["source_sentences"][0]["role"] == "title"
        assert res["source_coverage"] == 100.0
        assert r["acceptance_status"] == "accepted"

    def test_找不到存在性词时首句不是标题(self):
        """「库存管理要支持入库、出库」——「入库」藏在首句里，不能当标题丢掉"""
        s = _split_sentences("库存管理要支持入库、出库")
        assert s[0]["role"] == "goal"
        assert "入库" in s[0]["text"]


# ============================================================
# 约束挂得住（节点级 guard）
# ============================================================


class TestNodeLevelGuard:
    def test_单节点时约束挂节点上并投影成_rule_需求(self, store):
        """整张图只有一个节点 → 约束只能挂在节点自己的 guard 上

        旧契约：guard 只挂边 → 单节点时无处可挂 → 约束整个丢掉
        → 「必须先出快过期的批次」这条需求根本没进需求集 → WMS 概念绑不上。
        """
        r = _run_capture(
            store, "同一 SKU 出库必须先出快过期的批次",
            llm_result={"nodes": [
                {"action": "同一 SKU 出库", "guard": "必须先出快过期的批次",
                 "trace_to": "sent_1"},
            ]},
        )
        res = r["result"]
        assert res["nodes"][0]["guard"] == "必须先出快过期的批次"
        by_type = {q["requirement_type"]: q["description"]
                   for q in res["requirements"]}
        assert by_type["rule"] == "必须先出快过期的批次"
        assert by_type["process"] == "同一 SKU 出库"
        assert res["source_coverage"] == 100.0

    def test_节点级_guard_不是原文就被丢掉(self, store):
        """模型编的守卫不能进产出——跟边上的守卫一个规矩"""
        r = _run_capture(
            store, "同一 SKU 出库必须先出快过期的批次",
            llm_result={"nodes": [
                {"action": "同一 SKU 出库", "guard": "必须经理审批",
                 "trace_to": "sent_1"},
            ]},
        )
        assert r["result"]["nodes"][0]["guard"] == ""
        assert all(q["requirement_type"] != "rule"
                   for q in r["result"]["requirements"])


# ============================================================
# 「没说话」和「没干活」不许混成一件
# ============================================================


class TestEmptyVsModelFailure:
    def test_内容句存在但模型没产出节点_中止(self, store):
        """模型没干活 → 履约中止，不是一条好听的业务结论"""
        r = _run_capture(
            store, "建模退款流程。",
            llm_result={"goal": "建模退款流程", "nodes": [], "edges": []},
        )
        assert r["status"] == "failed"
        assert r["result"] is None
        bad = [s for s in r["op_trace"] if s["status"] == "failed"]
        assert bad and "no traceable node" in bad[0]["error"]

    def test_空表述_不调_llm_直接零需求(self, store, monkeypatch):
        """原文里一句内容都没有 → 不调 LLM，产出零需求交给 no_deliver

        让 LLM 对着空句子编东西，只会把幻觉引进来，
        然后被机械层剔光，最后以「模型失职」的面目中止——
        而真相是「业务方什么也没说」。这两件事必须分开。
        """
        from runtime import llm

        def _boom(*a, **kw):
            raise AssertionError("空表述不该调 LLM")

        monkeypatch.setattr(llm, "complete_json", _boom)
        r = trigger_task(
            "wms_requirement_box", "capture_business_requirement",
            {"narrative": "   "}, store,
        )
        assert r["status"] == "completed"
        assert r["result"]["requirement_count"] == 0
        assert r["acceptance_status"] == "rejected"


# ============================================================
# 溯源归位：goal 句里的片段也得能归位
# ============================================================


class TestAttachSources:
    def test_goal_句里的片段能归位(self):
        """实体 / 指标写在 goal 句里也要能归位

        旧契约只认 fact 句 → goal 句里的片段被当幻觉丢掉。
        """
        s = _split_sentences("同一 SKU 出库必须先出快过期的批次")
        assert _attach_sources(["同一 SKU"], s) == [("同一 SKU", "sent_1")]

    def test_title_句里的片段不归位(self):
        """名字句里读不出实体——那是编的"""
        s = _split_sentences("库存管理有入库流程、出库流程")
        assert _attach_sources(["库存管理"], s) == []


# ============================================================
# prompt 契约：机械层和 prompt 必须说同一件事
# ============================================================


class TestPromptContract:
    def test_prompt_要求_goal_句也抽节点(self):
        """prompt 必须写着「goal 句也要抽节点」

        这条不是查词在不在——查的是**整条指令**，
        删掉它 prompt 就回到「goal 不产出节点」的旧行为。
        """
        src = inspect.getsource(capture_runner)
        assert "每一条 [goal] / [fact] 句子都至少要有一个节点 trace_to 指向它" in src

    def test_prompt_说清_title_不抽节点(self):
        src = inspect.getsource(capture_runner)
        assert "它只是标题，**不要**为它抽节点" in src

    def test_prompt_说清单节点时约束挂节点(self):
        src = inspect.getsource(capture_runner)
        assert "约束就写进这个节点自己的 guard 字段" in src


# ============================================================
# schema：角色枚举得跟抽取层一致
# ============================================================


class TestRoleEnum:
    def test_role_枚举含_title_goal_fact(self):
        from boxes.requirement_capture.schemas import ALL_SCHEMAS

        schema = next(s for s in ALL_SCHEMAS if s.id == "schema_requirement_set_package")
        sent = next(f for f in schema.fields if f.name == "source_sentences")
        role = next(p for p in sent.items.properties if p.name == "role")
        assert set(role.values) == {"title", "goal", "fact"}
