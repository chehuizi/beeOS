"""守卫：落盘的 op 轨迹必须是盒子自己 beeline 声明的那条链

为什么要有这条守卫：
- 轨迹（op_trace）是**实际跑过什么**，beeline 声明是**应该跑什么**。
  两者不一致有两种可能：执行器走偏了（op_id 不在声明里），
  或者看板把「跑过两跳」说成「这只盒子只有两跳」。
  后一种不会报错，只会让人误判。
- 所以这里钉两件事：
  ① 轨迹的 op_id **必须是 beeline 声明的 op**，且顺序一致；
  ② 失败时轨迹是声明链的**前缀**（停在第 N 跳，后面的没跑到）——
     不是另一条链，也不是更短的一条「另一条链」。

为什么不用某只产品盒子当夹具：
「测机制」和「测某只盒子」必须分开（盒子会退役）。
这里用**产品盒子的真实声明**（跨产品不变式）+
**夹具盒的失败场景**（造一个必失败的 runner）。
"""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest

from kanban.trigger import BOX_REGISTRY, declared_beeline_ops
from kanban.web import dashboard_data
from runtime import BeelineExecutor, create_task_run
from runtime.runner import HandlerRunner
from runtime.store import TaskRunStore
from tests.fixtures.sample_box import get_beeline as get_sample_beeline
from tests.fixtures.sample_box import get_definition as get_sample_definition


def _boxes_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "boxes"


def _box_package_by_id(box_id: str) -> str | None:
    """按 definition 的 id 找到盒子自己的包名（不靠命名约定猜）"""
    for pkg in sorted(_boxes_dir().iterdir()):
        if not pkg.is_dir() or not (pkg / "definition.py").exists():
            continue
        try:
            mod = importlib.import_module(f"boxes.{pkg.name}.definition")
        except Exception:
            continue
        try:
            if mod.get_definition().id == box_id:
                return pkg.name
        except Exception:
            continue
    return None


# ---------- 声明本身 ----------

def test_every_registered_box_declares_a_nonempty_chain():
    """每只注册盒子都要有一条非空、不重复的 op 链

    空链 = 轨迹面板只能画「跑过的」，读者没法判断有没有漏跳。
    """
    for box_id in sorted(BOX_REGISTRY):
        ops = declared_beeline_ops(box_id)
        assert ops, f"{box_id} 没有声明 op 链"
        assert len(ops) == len(set(ops)), f"{box_id} 的 op 链里有重复：{ops}"


def test_the_declared_chain_comes_from_the_box_own_beeline():
    """看板读的声明链必须就是**盒子自己包里**的那条 beeline

    这条是独立查一遍的路径：看板从注册表拿，守卫从盒子自己的包拿。
    两边不一致 = 注册表指着一条过期/别人的 beeline，
    而看板上不会有任何报错。
    """
    for box_id in sorted(BOX_REGISTRY):
        pkg = _box_package_by_id(box_id)
        assert pkg, f"找不到 {box_id} 对应的盒子包"
        mod = importlib.import_module(f"boxes.{pkg}.beeline")
        own = [op.op_id for op in mod.get_beeline().operations]
        assert declared_beeline_ops(box_id) == own, (
            f"{box_id}：看板读到的声明链跟盒子自己的 beeline 不一致"
        )


def test_the_declared_chain_follows_the_version_the_run_used():
    """记录里用的是哪一版 beeline，就读哪一版的声明

    一只盒子可能有多版 beeline。取错版本，说出来的「应该跑几跳」就是错的。
    """
    box_id = "wms_analysis_box"
    entry = BOX_REGISTRY[box_id]
    beeline_id, loader = next(iter(entry[1].items()))
    assert declared_beeline_ops(box_id, beeline_id) == [
        op.op_id for op in loader().operations
    ]
    # 认不出的 beeline id → 退回这只盒子唯一那条（当前每只盒子就一条）
    assert declared_beeline_ops(box_id, "beeline_does_not_exist") == [
        op.op_id for op in loader().operations
    ]
    # 没注册的盒子 → 空链，不拿别的盒子的链凑
    assert declared_beeline_ops("no_such_box") == []


# ---------- 轨迹 vs 声明 ----------

def test_a_finished_run_covers_exactly_the_declared_chain():
    """跑完的履约：轨迹必须**恰好**是声明的那条链（不多不少、同序）

    用效果分析盒：它的 op 全是机械的，跑它不需要 LLM 凭证。
    """
    from boxes.wms_analysis import get_definition as get_analysis_def
    from boxes.wms_analysis.beeline import get_beeline as get_analysis_beeline
    from boxes.wms_analysis.runner import RUNNER as ANALYSIS_RUNNER

    box = get_analysis_def()
    beeline = get_analysis_beeline()
    tr = create_task_run(
        "o", "handle_effect_analysis_request", box.result.result_schema,
        f"{box.id}@v{box.version}", beeline.id, beeline.version, "rt", "in",
    )
    payload = {
        "analysis_id": "a1",
        "requirement_ref": "req_1",
        "expected_metrics": [{
            "expectation_id": "e1",
            "requirement_id": "req_1",
            "wms_kind": "fefo",
            "statement": "出库按批次失效日期升序取货",
        }],
        "observation_ref": {"observations": []},
        "observed_window": "2026-10-29",
    }
    tr = BeelineExecutor(ANALYSIS_RUNNER).execute(tr, beeline, payload)

    declared = [op.op_id for op in beeline.operations]
    ran = [rec.op_id for rec in tr.operations.values()]
    assert ran == declared, "跑完的履约，轨迹必须等于声明的链"
    assert declared_beeline_ops(box.id, beeline.id) == declared


def test_a_failed_run_is_a_prefix_of_the_declared_chain():
    """中途失败的履约：轨迹是声明链的前缀，失败那一跳标记 failed

    「前缀」很重要：失败不是「另一条链」，也不是「少了一跳的链」。
    """
    beeline = get_sample_beeline()
    declared = [op.op_id for op in beeline.operations]

    def _ok(input_data: dict[str, Any]) -> dict[str, Any]:
        return {"sample_id": "s1", "sample_state": "resolved", "steps": []}

    def _boom(input_data: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("这一跳故意炸")

    runner = HandlerRunner(
        {
            "sample_classify": _ok,
            "sample_resolve": _boom,
            "sample_notify": _ok,
            "sample_escalate": _ok,
        },
        owner="test",
    )
    box = get_sample_definition()
    tr = create_task_run(
        "o", "handle_sample_request", box.result.result_schema,
        f"{box.id}@v{box.version}", beeline.id, beeline.version, "rt", "in",
    )
    tr = BeelineExecutor(runner).execute(tr, beeline, {"sample_id": "s1"})

    ran = list(tr.operations.keys())
    assert ran == declared[:2], "失败的轨迹必须是声明链的前缀"
    assert tr.operations["sample_resolve"].status == "failed"
    assert "sample_notify" not in tr.operations

# ---------- 看板数据 ----------

def test_the_dashboard_carries_the_chain_next_to_the_trail(tmp_path: Path):
    """看板数据里既有「跑过什么」，也有「应该跑什么」

    少一边就会误判：只有轨迹 → 失败那次像「这只盒子只有两跳」；
    只有声明 → 面板看起来像「这些都跑过了」。
    """
    from boxes.wms_analysis import get_definition as get_analysis_def
    from boxes.wms_analysis.beeline import get_beeline as get_analysis_beeline
    from boxes.wms_analysis.runner import RUNNER as ANALYSIS_RUNNER

    store = TaskRunStore(path=tmp_path / "x.jsonl")
    box = get_analysis_def()
    beeline = get_analysis_beeline()
    tr = create_task_run(
        "o", "handle_effect_analysis_request", box.result.result_schema,
        f"{box.id}@v{box.version}", beeline.id, beeline.version, "rt", "in",
    )
    payload = {
        "analysis_id": "a1",
        "requirement_ref": "req_1",
        "expected_metrics": [{
            "expectation_id": "e1",
            "requirement_id": "req_1",
            "wms_kind": "fefo",
            "statement": "出库按批次失效日期升序取货",
        }],
        "observation_ref": {"observations": []},
        "observed_window": "2026-10-29",
    }
    tr = BeelineExecutor(ANALYSIS_RUNNER).execute(tr, beeline, payload)
    store.append(tr, box_id=box.id, acceptance_status="accepted")

    data = dashboard_data(store)
    trail = next(t for t in data["op_trails"] if t["box_id"] == box.id)
    declared = [op.op_id for op in beeline.operations]
    assert trail["declared_ops"] == declared
    assert [o["op_id"] for o in trail["ops"]] == declared
    assert trail["has_trace"] is True
    # 显式钉住包含关系：轨迹里**不许**出现 beeline 没声明的 op
    assert {o["op_id"] for o in trail["ops"]} <= set(trail["declared_ops"]), (
        "轨迹里出现了 beeline 没声明的 op"
    )


def test_a_record_without_a_trail_still_reports_no_trail(tmp_path: Path):
    """加落盘之前跑的记录：轨迹为空、has_trace=False

    声明的链照样给（那是盒子的事实），但**不许**把它画成「未执行」——
    我们不知道那次跑过什么，画「未执行」等于编造。
    """
    store = TaskRunStore(path=tmp_path / "y.jsonl")
    store.append(
        create_task_run("o", "i", "c", "r", "b", 1, "rt", "in"),
        box_id="wms_analysis_box",
        acceptance_status="accepted",
    )
    data = dashboard_data(store)
    trail = next(t for t in data["op_trails"] if t["box_id"] == "wms_analysis_box")
    assert trail["ops"] == []
    assert trail["has_trace"] is False
    assert trail["declared_ops"], "声明的链仍然要给——那是盒子自己的事实"
