"""
boxes.wms_analysis.runner - WMS Analysis Box 的 3 个 operation 实现

WMS 线上效果分析盒——拿真实运行数据回头看：当初那条需求，线上到底满足了没有。

**这条腿是真的了**：它读测试盒跑出来的实测观测，跟设计盒声明的预期
逐项比，落差归因到「哪条需求、哪个概念、证据在哪」，并播下下一轮需求的种子。

**三种「没结论」不许混成「达标」**：
- `not_applicable`：没有探针（没人验过）
- `indeterminate`：现场判不了（没条件验）
- 没观测到：这条预期压根没进比对

它们**都不算达标**。混进「达标」里，「什么都没验」就会显示成
「需求已满足」——那是最坏的一种输出：**看起来成功，其实什么都没验**。

**归因不重新发明**：实测的定位证据由探针给出（它知道 WMS 的哪张单据、
哪个批次有问题），分析盒只做**搬运 + 归口**，不自己编根因。
"""

from __future__ import annotations

from typing import Any

from runtime.runner import HandlerRunner


# 实测结论里哪些算「有确定结论」
DETERMINED_OUTCOMES = ("pass", "fail")
# 哪些算「没结论」（**都不算达标**）
UNRESOLVED_OUTCOMES = ("not_applicable", "indeterminate")


def _expected_metrics(input_data: dict[str, Any]) -> list[dict[str, Any]]:
    rows = input_data.get("expected_metrics")
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def _observations(input_data: dict[str, Any]) -> list[dict[str, Any]]:
    ref = input_data.get("observation_ref")
    if isinstance(ref, dict):
        rows = ref.get("observations")
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
    rows = input_data.get("observations")
    if isinstance(rows, list):
        return [r for r in rows if isinstance(r, dict)]
    return []


def analysis_intake_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 1：把预期和实测**对起来**

    按 `expectation_id` 配对。对不上的两种情况都要记下来：
    - 预期没有观测 → 这条**没验**
    - 观测没有预期 → 这是**孤儿观测**（上游多给了）

    静默丢掉任何一边，读者看到的「比了 N 条」就是假的。
    """
    expected = _expected_metrics(input_data)
    observations = _observations(input_data)
    obs_by_id = {
        o.get("expectation_id"): o for o in observations if o.get("expectation_id")
    }

    pairs: list[dict[str, Any]] = []
    unmatched_expectations: list[str] = []
    for exp in expected:
        eid = exp.get("expectation_id") or ""
        obs = obs_by_id.pop(eid, None)
        if obs is None:
            unmatched_expectations.append(eid)
        pairs.append({
            "expectation_id": eid,
            "requirement_id": exp.get("requirement_id", ""),
            "wms_kind": exp.get("wms_kind", ""),
            "statement": exp.get("statement", ""),
            "evidence": exp.get("evidence", ""),
            "observed": obs,
        })

    orphan_observations = [
        o.get("expectation_id", "") for o in obs_by_id.values()
    ]

    return {
        "analysis_id": input_data.get("analysis_id", ""),
        "requirement_ref": input_data.get("requirement_ref", ""),
        "observed_window": input_data.get("observed_window", ""),
        # ---- 往下带（下一跳的输入就是这一跳的输出）----
        "expected_metrics": expected,
        "observation_ref": input_data.get("observation_ref", {}),
        # ---- 配对结果 ----
        "pairs": pairs,
        "unmatched_expectations": unmatched_expectations,
        "orphan_observations": orphan_observations,
        "compared_metric_count": len(pairs),
    }


def _classify(pair: dict[str, Any]) -> str:
    """这条预期是「达标 / 落差 / 没结论 / 没验」"""
    obs = pair.get("observed")
    if not obs:
        return "not_observed"
    outcome = obs.get("outcome")
    if outcome == "pass":
        return "satisfied"
    if outcome == "fail":
        return "gap"
    if outcome in UNRESOLVED_OUTCOMES:
        return "unresolved"
    # 认不出的 outcome → **当没结论**，不猜。
    return "unresolved"


def _seed_of(pair: dict[str, Any], obs: dict[str, Any]) -> str:
    """下一轮需求的种子

    **机械拼装**，不发挥：这条要求是什么（statement）+
    业务原话是什么（evidence）+ 实测看到了什么（summary）。
    种子要能直接变成下一轮的一条需求，所以必须点名**哪个要求没达成**。
    """
    concept = pair.get("wms_kind") or ""
    statement = pair.get("statement") or ""
    evidence = pair.get("evidence") or ""
    summary = obs.get("summary") or ""
    parts = ["【待满足】"]
    if concept:
        parts.append(f"{concept}：")
    if statement:
        parts.append(f"{statement}")
    if evidence:
        parts.append(f"（业务原话：{evidence}）")
    parts.append(f"—— 本轮实测：{summary or '未达成'}")
    return "".join(parts)


def analysis_compare_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 2：逐项比对 + 落差归因

    **归因必须带定位信息**：落差不能只有一个布尔值，
    读者要知道是哪条需求、哪个概念、证据在哪（探针给的 details）。
    """
    pairs = input_data.get("pairs") or []
    satisfied: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    not_observed: list[dict[str, Any]] = []

    for pair in pairs:
        kind = _classify(pair)
        obs = pair.get("observed") or {}
        if kind == "satisfied":
            satisfied.append(pair)
        elif kind == "gap":
            gaps.append({
                "expectation_id": pair.get("expectation_id", ""),
                "requirement_id": pair.get("requirement_id", ""),
                "wms_kind": pair.get("wms_kind", ""),
                "statement": pair.get("statement", ""),
                "evidence": pair.get("evidence", ""),
                "summary": obs.get("summary", ""),
                "details": obs.get("details") or {},
                "seed": _seed_of(pair, obs),
            })
        elif kind == "not_observed":
            not_observed.append(pair)
        else:
            unresolved.append(pair)

    # **收敛了吗**：有确定结论、且没有落差。
    # 一条确定结论都没有 → 不算收敛（"什么都没验"不是"满足"）。
    determined = len(satisfied) + len(gaps)
    converged = determined > 0 and not gaps

    # **可追溯**：每条比对项都要指回一条需求。
    requirement_traceability = all(
        bool(p.get("requirement_id")) for p in pairs
    )

    # **归因到位**：每条落差都要有归因（概念 + 表述 + 实测摘要 + 证据）。
    gap_attribution = all(
        bool(g.get("wms_kind")) and bool(g.get("summary")) and bool(g.get("seed"))
        for g in gaps
    )

    # **播种**：有落差就要有种子。
    # 没有落差时，「都播种了」是**空真**——没有下一轮需求可播，
    # 不是「播了但漏了」。
    next_requirement_seeded = all(bool(g.get("seed")) for g in gaps)

    return {
        "analysis_id": input_data.get("analysis_id", ""),
        "requirement_ref": input_data.get("requirement_ref", ""),
        "observed_window": input_data.get("observed_window", ""),
        "expected_metrics": input_data.get("expected_metrics", []),
        "observation_ref": input_data.get("observation_ref", {}),
        # ---- 往下带 ----
        "pairs": pairs,
        "unmatched_expectations": input_data.get("unmatched_expectations", []),
        "orphan_observations": input_data.get("orphan_observations", []),
        # ---- 比对结果 ----
        "satisfied": satisfied,
        "gaps": gaps,
        "unresolved": unresolved,
        "not_observed": not_observed,
        "compared_metric_count": input_data.get("compared_metric_count", 0),
        "gap_count": len(gaps),
        "converged": converged,
        "requirement_traceability": requirement_traceability,
        "gap_attribution": gap_attribution,
        "next_requirement_seeded": next_requirement_seeded,
        "next_requirement_seed": gaps[0]["seed"] if gaps else "",
    }


def analysis_packager_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 3：打包成效果分析记录

    **「收敛率」这个字段名其实是布尔**（schema 里声明的是 boolean）——
    名字叫 rate，类型是 bool，读起来别扭。这里如实按 bool 产出：
    「收敛了吗」。真要比率，另外加字段，不在布尔字段上改名。
    """
    gaps = input_data.get("gaps") or []
    satisfied = input_data.get("satisfied") or []
    unresolved = input_data.get("unresolved") or []
    not_observed = input_data.get("not_observed") or []

    return {
        "analysis_id": input_data.get("analysis_id", ""),
        "requirement_ref": input_data.get("requirement_ref", ""),
        "observed_window": input_data.get("observed_window", ""),
        # ---- 判据读的量 ----
        "compared_metric_count": input_data.get("compared_metric_count", 0),
        "gap_count": len(gaps),
        "convergence_rate": input_data.get("converged", False),
        "requirement_traceability": input_data.get("requirement_traceability", False),
        "gap_attribution": input_data.get("gap_attribution", False),
        "next_requirement_seeded": input_data.get("next_requirement_seeded", False),
        # ---- 分项（读者要知道比了几条、过了几条、漏了几条）----
        "passed_count": len(satisfied),
        "failed_count": len(gaps),
        "not_observed_count": len(unresolved) + len(not_observed),
        # ---- 落差逐条带定位证据 ----
        "gaps": gaps,
        "next_requirement_seed": input_data.get("next_requirement_seed", ""),
        "unmatched_expectations": input_data.get("unmatched_expectations", []),
        "orphan_observations": input_data.get("orphan_observations", []),
    }


WMS_ANALYSIS_HANDLERS: dict[str, Any] = {
    "analysis_intake": analysis_intake_handler,
    "analysis_compare": analysis_compare_handler,
    "analysis_packager": analysis_packager_handler,
}

RUNNER = HandlerRunner(WMS_ANALYSIS_HANDLERS, owner="wms_analysis_box")
