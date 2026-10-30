"""守卫：工作项的归属不能丢——谁造的、属于哪条链

三个真问题，一个族：
- 需求盒**造出**需求集，但它的记录 subject_ref 是空的 → 它造出来的
  工作单元没有「谁造的」这一条 → 看板上那条需求任务**少了一只盒子**
  （用户原话：「这个任务为啥只用了 4 个 box？」）。
- 链上运维那一跳处理的是**运维事件**，挂在自己的事件号上 → 一条 6 只
  盒子的链在归档里断成两件互不相干的活儿。
- 所以要两样东西：**产出认领**（subject_ref_from_result）和
  **链归属**（chain_ref / chain_kind，编排者声明）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from kanban.trigger import trigger_task
from kanban.web import _collect_tasks
from core.models import TaskDef
from runtime.store import TaskRunStore


# ---------- 声明 ----------

def test_a_task_that_produces_the_work_item_must_declare_its_kind():
    """声明了「标识从产出里取」就必须声明种类——不能靠猜

    不声明就等于让运行时猜「产出里那个键说的是哪一类业务对象」，
    猜错了没人会发现（归档里两类记录混在一起，看板上还是「一切正常」）。
    """
    with pytest.raises(Exception):
        TaskDef(
            type="t",
            task_schema="s",
            beeline_id="b",
            beeline_version=1,
            subject_ref_from_result="set_id",
        )
    ok = TaskDef(
        type="t",
        task_schema="s",
        beeline_id="b",
        beeline_version=1,
        subject_ref_from_result="set_id",
        subject_kind="requirement",
    )
    assert ok.subject_kind == "requirement"


def test_the_requirement_box_declares_that_it_claims_what_it_produced():
    """需求盒的声明要真的在位（这句话以前只是注释里的愿望）"""
    from boxes.wms_requirement import get_definition

    task = get_definition().get_task("capture_business_requirement")
    assert task.subject_ref_from_result == "set_id", (
        "需求盒造出需求集——它得声明「产出里的 set_id 就是它认领的工作项」"
    )
    assert task.subject_kind == "requirement"


# ---------- 产出认领（真跑一次需求盒） ----------

def test_the_run_that_created_the_work_item_claims_it(tmp_path: Path, monkeypatch):
    """需求盒跑完之后，它的那条记录要认领自己造出来的需求集

    这是「为什么这个任务只有 4 只盒子」的直接修复：
    认领之前，这条记录 subject_ref 是空的。
    """
    from runtime import llm
    from tests.conftest import stub_llm_dispatch

    monkeypatch.setattr(llm, "complete_json", stub_llm_dispatch)

    store = TaskRunStore(path=tmp_path / "x.jsonl")
    cap = trigger_task(
        "wms_requirement_box", "capture_business_requirement",
        {"narrative": "增加库区管理功能：库区是仓库下的分区，库位归属在库区上。"},
        store,
    )
    set_id = cap["result"]["set_id"]
    record = store.list_recent()[0]
    assert record["subject_ref"] == set_id, (
        "需求盒认领了自己造出来的需求集——它就是这条工作项的来处"
    )
    assert record["subject_kind"] == "requirement"
    # 触发结果里也带出来：编排者拿它认领「这条链」，
    # 不在编排层另编一个 kind（编了就等于在脚本里声明业务事实）。
    assert cap["subject_ref"] == set_id
    assert cap["subject_kind"] == "requirement"


def test_a_failed_run_is_not_blamed_on_the_claim_and_still_leaves_a_record(
    tmp_path: Path, monkeypatch,
):
    """失败的履约：原因照旧，**记录不许消失**

    「产出认领」是给**跑完**的履约用的。失败的履约没有产出，
    也就没有可认领的工作单元——这时报「产出里没有 set_id」是
    拿不相干的错盖掉真正的失败原因（LLM 不可用 → 履约中止），
    而且记录一丢，这次失败就没人看得见了。
    """
    from runtime import llm

    def _boom(*a, **kw):
        raise llm.LLMError("simulated outage")

    monkeypatch.setattr(llm, "complete_json", _boom)

    store = TaskRunStore(path=tmp_path / "y.jsonl")
    r = trigger_task(
        "wms_requirement_box", "capture_business_requirement",
        {"narrative": "库存数量不能为负。"}, store,
    )
    assert r["status"] == "failed"
    bad = [o for o in r["op_trace"] if o["status"] == "failed"]
    assert bad and "simulated outage" in bad[0]["error"], (
        "报的应该是真正的失败原因，不是「产出里没有 set_id」"
    )
    records = store.list_recent()
    assert records, "这次失败的履约记录不见了——凭空消失 = 没人看得见它挂过"
    assert records[0]["status"] == "failed"
    assert records[0]["subject_ref"] is None, "没产出就没有可认领的工作单元"


# ---------- 链归属 ----------

def _rec(box_id: str, subject_ref: str | None, subject_kind: str | None = "requirement",
         chain_ref: str | None = None, chain_kind: str | None = None,
         at: str = "2026-10-29T10:00:00+00:00") -> dict:
    return {
        "task_run_id": f"{box_id}-{subject_ref}-{chain_ref}-{at}",
        "box_id": box_id,
        "status": "completed",
        "acceptance_status": "accepted",
        "created_at": at,
        "subject_ref": subject_ref,
        "subject_kind": subject_kind if subject_ref else None,
        "chain_ref": chain_ref,
        "chain_kind": chain_kind,
        "op_trace": [],
    }


def test_a_chain_is_one_work_item_even_when_a_hop_handles_another_object():
    """一条链 = 一件活儿：链上那一跳处理的业务对象不同，也归同一条链

    运维那一跳处理的是**运维事件**（ops_xxx），不是需求集。
    只按业务对象分组的话，这条链在归档里就断成两件活儿：
    需求集那条少了运维，运维事件那条只有一只盒子。
    """
    records = [
        # 链的起点：需求盒认领自己造出来的需求集
        _rec("wms_requirement_box", "req_1", "requirement"),
        _rec("wms_design_box", "req_1", "requirement"),
        _rec("wms_development_box", "req_1", "requirement"),
        _rec("wms_testing_box", "req_1", "requirement"),
        # 链上的运维那一跳：业务对象是**运维事件**，但属于这条链
        _rec("wms_ops_box", "ops_1", "ops_event",
             chain_ref="req_1", chain_kind="requirement"),
        _rec("wms_analysis_box", "req_1", "requirement",
             chain_ref="req_1", chain_kind="requirement"),
    ]
    tasks = _collect_tasks(records)
    assert len(tasks) == 1, (
        f"一条链应该是一件活儿，实际拆成了 {len(tasks)} 件：{[t['ref'] for t in tasks]}"
    )
    task = tasks[0]
    assert task["ref"] == "req_1"
    assert len(task["boxes"]) == 6, (
        f"这条链牵了 6 只盒子，实际 {len(task['boxes'])} 只"
    )
    assert {b["box_id"] for b in task["boxes"]} == {
        "wms_requirement_box", "wms_design_box", "wms_development_box",
        "wms_testing_box", "wms_ops_box", "wms_analysis_box",
    }


def test_a_standalone_run_stays_its_own_work_item():
    """没有链归属的运行仍按业务对象分组（别把不相干的事并成一件）"""
    records = [
        _rec("wms_ops_box", "ops_9", "ops_event"),
        _rec("wms_ops_box", "ops_8", "ops_event", at="2026-10-29T11:00:00+00:00"),
    ]
    tasks = _collect_tasks(records)
    assert {t["ref"] for t in tasks} == {"ops_9", "ops_8"}


def test_the_chain_is_recorded_on_the_run():
    """链归属要落进记录——不然下次归档又得靠猜"""
    from runtime.models import create_task_run

    tr = create_task_run(
        "o", "i", "c", "r", "b", 1, "rt", "in",
        subject_ref="ops_1", subject_kind="ops_event",
        chain_ref="req_1", chain_kind="requirement",
    )
    assert tr.identity.chain_ref == "req_1"
    assert tr.identity.chain_kind == "requirement"
