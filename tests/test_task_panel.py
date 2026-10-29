"""守卫：左栏是「任务」，中间是「场」，状态长在盒子上

为什么要有这一组：
- 左栏原来列的是**盒子**——而中间那栏（场）已经把盒子全画出来了。
  同一份信息在两处，读者得来回拼才拼得出全貌。左栏搬成「任务」
  （哪件需求在飞、牵过哪些盒子、各自跑成什么样）之后，两栏各答一件事。
- **状态要长在盒子上**：状态是盒子的属性（这只盒子现在怎么样）。
  放在任务列表里就成了「任务旁边一列盒子的状态」——同一个信息两处，
  迟早对不上。
- 选了任务以后，中间和右栏都得**换成这件任务的口径**。口径不跟着走，
  读者在问「这件任务走到哪了」，看到的却是另一件任务的数字。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from kanban.web import HTML_PAGE, dashboard_data
from runtime import BeelineExecutor, create_task_run
from runtime.store import TaskRunStore


def _record(box_id: str, subject_ref: str | None, *, status: str = "completed",
            acceptance: str = "accepted", at: str = "2026-10-28T10:00:00+00:00",
            subject_kind: str = "requirement", **extra: Any) -> dict[str, Any]:
    rec = {
        "task_run_id": f"{box_id}-{subject_ref}-{at}",
        "box_id": box_id,
        "beeline_id": "beeline_wms_analysis_v1",
        "beeline_version": 1,
        "intent": "handle_effect_analysis_request",
        "originator": "test",
        "status": status,
        "acceptance_status": acceptance,
        "created_at": at,
        "started_at": at,
        "finished_at": at,
        "duration_ms": 1,
        "op_count": 1,
        "exception_count": 0,
        "error_kind": None,
        "error_reason": None,
        "observed": {},
        "rule_applied": None,
        "rejection_class": None,
        "subject_ref": subject_ref,
        "subject_kind": subject_kind if subject_ref else None,
        "op_trace": [],
        "artifact": None,
    }
    rec.update(extra)
    return rec


def _store(tmp_path: Path, records: list[dict[str, Any]]) -> TaskRunStore:
    store = TaskRunStore(path=tmp_path / "x.jsonl")
    with store.path.open("w", encoding="utf-8") as f:
        import json
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    store._load()
    return store


# ---------- 任务是怎么归出来的 ----------

def test_the_same_work_item_is_one_task(tmp_path: Path):
    """同一个工作项号 = 一件任务，多轮履约都算它的

    归成「每次履约一个任务」的话，一件需求跑三轮就变成三件任务——
    读者看到三行同名的东西，还以为有三件不同的需求。
    """
    store = _store(tmp_path, [
        _record("wms_requirement_box", "req_1", at="2026-10-28T10:00:00+00:00"),
        _record("wms_design_box", "req_1", at="2026-10-28T10:01:00+00:00"),
        _record("wms_design_box", "req_1", at="2026-10-28T11:01:00+00:00"),
        _record("wms_ops_box", "req_2", at="2026-10-28T12:00:00+00:00"),
    ])
    data = dashboard_data(store)
    tasks = {t["ref"]: t for t in data["tasks"]}
    assert set(tasks) == {"req_1", "req_2"}
    assert tasks["req_1"]["runs"] == 3, "三轮履约还是一件任务"
    assert len(tasks["req_1"]["boxes"]) == 2, "每只盒子只占一行"


def test_a_box_shows_its_latest_run_inside_the_task(tmp_path: Path):
    """任务里每只盒子只取**最近一次**——修好的任务不该永远挂着红"""
    store = _store(tmp_path, [
        _record("wms_development_box", "req_1", status="failed",
                acceptance="rejected", at="2026-10-28T10:00:00+00:00"),
        _record("wms_development_box", "req_1", status="completed",
                acceptance="accepted", at="2026-10-28T11:00:00+00:00"),
    ])
    data = dashboard_data(store)
    task = next(t for t in data["tasks"] if t["ref"] == "req_1")
    assert task["state"] == "done", "最近一次已经跑通，不该还标成有失败"
    box = task["boxes"][0]
    assert box["status"] == "completed"
    assert box["at"] == "2026-10-28T11:00:00+00:00"


def test_a_failed_box_makes_the_task_red(tmp_path: Path):
    """有一只盒子最近一次是挂了 → 这件任务标红"""
    store = _store(tmp_path, [
        _record("wms_requirement_box", "req_1"),
        _record("wms_development_box", "req_1", status="failed",
                acceptance="rejected", at="2026-10-28T11:00:00+00:00"),
    ])
    data = dashboard_data(store)
    task = next(t for t in data["tasks"] if t["ref"] == "req_1")
    assert task["state"] == "failed"
    assert task["failed"] == 1


def test_runs_without_a_work_item_are_not_tasks(tmp_path: Path):
    """没有工作项号的运行**不进**任务列表

    硬归成一个「(无主题)」的假任务，等于凭空造一件读者没法核对的任务——
    那比不列更坏。它们仍然是盒子的运行记录（中间/右栏看得到）。
    """
    store = _store(tmp_path, [
        _record("receipt_capture_box", None, at="2026-10-18T10:00:00+00:00"),
        _record("wms_system_box", None, at="2026-10-18T11:00:00+00:00"),
        _record("wms_requirement_box", "req_1"),
    ])
    data = dashboard_data(store)
    assert [t["ref"] for t in data["tasks"]] == ["req_1"]


def test_the_task_carries_a_human_readable_title(tmp_path: Path):
    """任务列表要能一眼看出「这任务是干什么的」

    标题取自产出本体里业务方自己写的那句话（需求盒的 document.title），
    不是让读者对着一串 req_set_nl_xxx 发呆。
    """
    store = _store(tmp_path, [
        _record("wms_requirement_box", "req_1", artifact={
            "document": {"title": "增加库区管理功能：库区是仓库下的分区"},
        }),
    ])
    data = dashboard_data(store)
    task = next(t for t in data["tasks"] if t["ref"] == "req_1")
    assert task["label"] == "增加库区管理功能：库区是仓库下的分区"


# ---------- 选了任务以后，中间和右栏的口径 ----------

def test_selecting_a_task_limits_the_middle_to_its_boxes(tmp_path: Path):
    """选中任务 → 中间只摆这件任务牵过的盒子

    多摆没参与过的盒子只会稀释「这件任务走到哪了」这个答案。
    """
    store = _store(tmp_path, [
        _record("wms_requirement_box", "req_1"),
        _record("wms_design_box", "req_1"),
        _record("wms_ops_box", "req_2"),
        _record("wms_analysis_box", None, at="2026-10-27T10:00:00+00:00"),
    ])
    data = dashboard_data(store, task_ref="req_1")
    assert data["task_filter"] == "req_1"
    assert sorted(b["box_id"] for b in data["box_stats"]) == [
        "wms_design_box", "wms_requirement_box",
    ]


def test_selecting_a_task_scopes_the_trail_to_that_run(tmp_path: Path):
    """选中任务 → 右栏给的是**这件任务里**那次运行的轨迹

    读者在问「这件任务走到哪了」，给他看这只盒子全局最近一次的轨迹，
    等于答非所问——尤其是这只盒子后来又替别的任务跑过。
    """
    store = _store(tmp_path, [
        _record("wms_analysis_box", "req_1",
                at="2026-10-28T10:00:00+00:00",
                op_trace=[{"op_id": "analysis_intake", "status": "completed",
                           "error": "", "error_kind": "",
                           "started_at": None, "finished_at": None}]),
        _record("wms_analysis_box", "req_2",
                at="2026-10-28T12:00:00+00:00",
                op_trace=[{"op_id": "other_run", "status": "completed",
                           "error": "", "error_kind": "",
                           "started_at": None, "finished_at": None}]),
    ])
    scoped = dashboard_data(store, task_ref="req_1")
    trail = next(t for t in scoped["op_trails"] if t["box_id"] == "wms_analysis_box")
    assert [o["op_id"] for o in trail["ops"]] == ["analysis_intake"]

    unscoped = dashboard_data(store)
    trail2 = next(t for t in unscoped["op_trails"] if t["box_id"] == "wms_analysis_box")
    assert [o["op_id"] for o in trail2["ops"]] == ["other_run"], (
        "不选任务时，右栏给的是这只盒子最近一次"
    )


def test_the_box_carries_its_status_for_the_current_scope(tmp_path: Path):
    """盒子上要能画出状态，且**三种状态分开**

    「跑挂了」和「跑完但判红」合成一个「失败」，就分不出
    「代码写不出来」和「写出来了但不对」。
    """
    store = _store(tmp_path, [
        _record("wms_analysis_box", "req_1", status="failed", acceptance="rejected"),
    ])
    data = dashboard_data(store, task_ref="req_1")
    box = next(b for b in data["box_stats"] if b["box_id"] == "wms_analysis_box")
    assert box["task_run"]["status"] == "failed"
    assert box["task_run"]["acceptance_status"] == "rejected"
    # 不选任务时，同一只盒子用的是它**全局**最近一次
    box2 = next(b for b in dashboard_data(store)["box_stats"]
                 if b["box_id"] == "wms_analysis_box")
    assert box2["last_run_state"] == "failed"
    assert box2["task_run"] is None


# ---------- 前端结构 ----------

def _js_function(name: str) -> str:
    """从 HTML_PAGE 里抠出某个 JS 函数的完整源码（按花括号配平）"""
    start = HTML_PAGE.index(f"\nfunction {name}(") + 1
    i = HTML_PAGE.index("{", start)
    depth = 0
    for j in range(i, len(HTML_PAGE)):
        if HTML_PAGE[j] == "{":
            depth += 1
        elif HTML_PAGE[j] == "}":
            depth -= 1
            if depth == 0:
                return HTML_PAGE[start:j + 1]
    raise AssertionError(f"{name} 的花括号没配平")


def test_the_status_badge_lives_on_the_box_cards():
    """状态要画在**中间的盒子上**，不是只在左栏

    盒子的状态长在任务列表里，就成了「任务旁边一列盒子的状态」——
    同一个信息两处，迟早对不上。所以卡片上必须有状态徽标。
    """
    pill = _js_function("boxStatePill")
    for fn in ("compactCaptionHtml", "captionHtml"):
        assert "boxStatePill(" in _js_function(fn), (
            f"{fn} 里没有状态徽标——状态没长在盒子上"
        )
    # 三种状态分开说，不许合成一个「失败」
    assert "failed" in pill and "rejected" in pill and "accepted" in pill
    assert "跑挂" in pill and "判红" in pill, (
        "「跑挂了」和「跑完但判红」必须分开说"
    )


def test_the_topology_view_is_not_used_for_a_task():
    """选了任务不画拓扑：拓扑画的是域的接线（环），不是这件任务

    环上缺段还叫拓扑，那是骗人。所以拓扑 chip 标「不适用」，
    中间改画平铺，并写明原因。
    """
    filters = _js_function("renderFilters")
    assert "_task" in filters, "筛选条不知道有没有选任务"
    assert "不适用" in filters, "选了任务时拓扑 chip 要标「不适用」并说明原因"
    assert "topology" in HTML_PAGE
    # 中间的渲染里，选了任务时不走环形分支
    assert "const topology = (" in HTML_PAGE, "中间没按「选了任务」决定画不画环"


def test_the_task_selection_lives_in_the_url():
    """任务的选择要进 URL：能分享、能后退

    以前域和视图都进了 URL，任务不进的���话——点进去再退回来，
    「看的是哪件任务」就丢了。
    """
    assert "taskHref" in HTML_PAGE
    href = _js_function("taskHref")
    assert "'/?task=' + encodeURIComponent(ref)" in href, (
        "任务链接必须带 task 参数"
    )
    assert "params.push('task=' + encodeURIComponent(task))" in HTML_PAGE, (
        "取数时要按 task 参数过滤口径"
    )
