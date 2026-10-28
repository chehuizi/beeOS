"""补两条守卫：落盘的记录必须带 op 轨迹 + 轨迹能进看板数据。

为什么要有：
- **op 轨迹以前只活在 trigger 那一瞬间的 HTTP 响应里**。请求一关就没了，
  刷新一下页面就再也问不出「这只盒子刚才跑了哪几步」。
  跟产出本体不落盘是同一族病（那边已有注释说明为什么必须落盘）。
- 右侧「操作轨迹」面板的数据来自落盘，不是来自前端刨 recent。
"""
from __future__ import annotations

from pathlib import Path

from kanban.web import dashboard_data
from runtime import BeelineExecutor, create_task_run
from runtime.store import TaskRunStore
from tests.fixtures.sample_box import get_definition, get_beeline
from tests.fixtures.sample_box.runner import RUNNER


def _run_one(tmp_path: Path) -> dict:
    """跑一次履约并落盘，返回那条 record"""
    store = TaskRunStore(path=tmp_path / "x.jsonl")
    box = get_definition()
    beeline = get_beeline()
    tr = create_task_run(
        "o", "handle_sample_request", box.result.result_schema,
        f"{box.id}@v{box.version}", beeline.id, beeline.version, "rt", "in",
    )
    tr = BeelineExecutor(RUNNER).execute(tr, beeline, {"sample_id": "s1"})
    store.append(tr, box_id=box.id, acceptance_status="accepted")
    return store.list_recent()[0]


def test_the_record_carries_the_op_trail(tmp_path: Path):
    """每个 op 的执行事实要落盘：op_id / 状态 / 错误 / 时间"""
    record = _run_one(tmp_path)
    trace = record["op_trace"]
    assert [o["op_id"] for o in trace] == [
        "sample_classify", "sample_resolve", "sample_notify",
    ], "op 轨迹必须按执行顺序落盘"
    assert all(o["status"] == "completed" for o in trace)
    # 字段齐全：轨迹要能回答「哪一步挂了、挂在什么设施上」
    for op in trace:
        assert set(op) >= {"op_id", "status", "error", "error_kind",
                           "started_at", "finished_at"}


def test_the_trail_reaches_the_dashboard_payload(tmp_path: Path):
    """看板数据里要有 op_trails，前端才画得出右侧轨迹面板

    用一只**已注册**的盒子（效果分析盒）：看板只展示注册表里的盒子，
    夹具盒的记录会被诚实地过滤掉——那是对的，所以这里不能用夹具盒。
    """
    from boxes.wms_analysis import get_definition as get_analysis_def
    from boxes.wms_analysis.beeline import get_beeline as get_analysis_beeline
    from boxes.wms_analysis.runner import RUNNER as ANALYSIS_RUNNER

    store = TaskRunStore(path=tmp_path / "y.jsonl")
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
    store.append(tr, box_id=box.id, acceptance_status="rejected")

    data = dashboard_data(store)
    assert "op_trails" in data, "看板数据里没有 op_trails——右侧面板就没数据源"
    trails = {t["box_id"]: t for t in data["op_trails"]}
    assert box.id in trails, "已注册盒子的轨迹没进 payload"
    assert [o["op_id"] for o in trails[box.id]["ops"]] == [
        "analysis_intake", "analysis_compare", "analysis_packager",
    ]


def test_a_record_without_a_trail_is_reported_as_such(tmp_path: Path):
    """旧记录没有轨迹就说没有——不拿别的数来凑"""
    store = TaskRunStore(path=tmp_path / "z.jsonl")
    store.append(
        create_task_run("o", "i", "c", "r", "b", 1, "rt", "in"),
        box_id="wms_design_box",
        acceptance_status="accepted",
    )
    data = dashboard_data(store)
    trails = {t["box_id"]: t for t in data["op_trails"]}
    # 记录里没有 op_trace 字段 → 轨迹为空数组（不是伪造的几条）
    assert trails["wms_design_box"]["ops"] == []
