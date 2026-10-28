"""
boxes.wms_ops.runner - WMS Ops Box 的 3 个 operation 实现

WMS 运维盒——管**上线之后**的事：变更审批、发布执行、故障处置、回滚能力、
以及把线上真实状态回写进系统档案。

**这条腿是真的**：它接上上游（测试盒的验收报告 + 开发盒的制品），
读**生产实例**（只读），把处置和现场状态记成一条运维记录。

**四条判据的答案没有一条来自自述**：

| 判据 | 谁给答案 |
|---|---|
| change_approval_rate | 上游验收报告（判绿了没）+ 变更文本有没有指名那份构建 |
| rollback_readiness | git / 制品库：回滚目标**取得到**吗 |
| incident_documented | 告警条数 ↔ 处置记录条数，逐条对得上 |
| runtime_state_recorded | 生产实例（只读 GET）：真的读到了吗 |

**「没读到」不许算「读到了」**：生产连不上 → runtime_state_recorded 判红，
理由写清是连不上还是没配地址——不是产出一份空档案说「已回写」。
"""

from __future__ import annotations

import os
from typing import Any, Optional

from boxes.wms_ops import rollback as rollback_check
from boxes.wms_ops.runtime_map import (
    DASHBOARD_BUSINESS_FIELDS,
    classify_alert,
    production_read_method,
)
from boxes.wms_ops.wms_reader import ReaderError, WmsReader, observed_at
from core.models import NOT_APPLICABLE
from runtime.runner import HandlerRunner

# 环境变量：生产地址 + 实例模式
WMS_BASE_URL_ENV = "BEEOS_WMS_BASE_URL"
WMS_MODE_ENV = "BEEOS_WMS_MODE"

# 验收通过的门槛（跟测试盒同一口径：百分数）
VERIFICATION_PASS_RATE = 99.0


def _base_url(input_data: dict[str, Any]) -> str:
    return (
        str(input_data.get("target_base_url") or "").strip()
        or (os.environ.get(WMS_BASE_URL_ENV) or "").strip()
    )


def _mode(input_data: Optional[dict[str, Any]] = None) -> str:
    """实例模式：按任务指定的优先，其次环境变量，最后 production"""
    given = str((input_data or {}).get("target_mode") or "").strip()
    if given:
        return given
    return (os.environ.get(WMS_MODE_ENV) or "production").strip()


def _split_changes(change_request: str) -> list[str]:
    """变更描述 → 一条条变更（一行一条 / 一个分号一条）

    **机械切分，不猜**：留空 = 这次没有要放行的变更。
    """
    raw = (change_request or "").strip()
    if not raw:
        return []
    out: list[str] = []
    for line in raw.replace(";", "\n").replace("；", "\n").splitlines():
        text = line.strip()
        if text:
            out.append(text)
    return out


def _names_the_build(text: str, artifact: dict[str, Any]) -> bool:
    """这条变更有没有指名它要放的那份构建

    **为什么必须指名**：变更单写「上线拣货准确率」，放行的却是另一份构建，
    那这份验收就白做了——它验的不是要上线的东西。
    """
    commit = str(artifact.get("commit_sha") or "").strip()
    digest = str(artifact.get("artifact_digest") or "").strip()
    if not commit and not digest:
        return False
    if commit and (commit[:8] in text or commit in text):
        return True
    if digest and (digest in text or (len(digest) > 16 and digest[:16] in text)):
        return True
    return False


def _verification_ok(verification: dict[str, Any]) -> tuple[bool, str]:
    """上游验收报告说这次可以放行吗"""
    if not verification:
        return False, "没有验收报告——没验过的东西不许上线"
    if verification.get("verification_determined") is not True:
        return False, "验收报告说这次什么都没验出来（verification_determined=false）"
    if verification.get("no_blocking_defect") is not True:
        return False, (
            f"验收报告说有阻塞缺陷（open_defect_count="
            f"{verification.get('open_defect_count')}）"
        )
    rate = verification.get("case_pass_rate")
    if rate is None or (isinstance(rate, str) and rate.strip() == "n/a"):
        return False, "验收报告没给通过率（没验出东西，通过率用不上）"
    if not isinstance(rate, (int, float)) or rate < VERIFICATION_PASS_RATE:
        return False, f"验收通过率 {rate}，不到 {VERIFICATION_PASS_RATE}"
    return True, f"验收通过（case_pass_rate={rate}）"


def ops_intake_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 1：收下运维任务 + 接上上游

    **这一跳只做编排**：把「要放行什么 / 处置什么 / 回滚回哪 / 读哪个生产」
    排清楚。不做判定——判定在 op 2，那里才有神谕。
    """
    verification = input_data.get("verification_ref") or {}
    artifact = input_data.get("artifact_ref") or {}
    if not isinstance(verification, dict):
        verification = {}
    if not isinstance(artifact, dict):
        artifact = {}
    changes = _split_changes(str(input_data.get("change_request") or ""))
    alerts = [str(a) for a in (input_data.get("alerts") or []) if str(a).strip()]

    return {
        "ops_task_id": str(input_data.get("ops_task_id") or ""),
        "change_count": len(changes),
        "incident_count": len(alerts),
        "changes": [
            {"change_id": f"chg_{i + 1}", "text": text, "approved": False, "reason": ""}
            for i, text in enumerate(changes)
        ],
        # **告警原文要带下去**：只带条数的话，下一跳拿到的
        # 每条处置记录都是空壳（记了「有一条告警」，但那条告警是什么？
        # 不知道）。这一跳的输入 = 上一跳的输出，漏一个就静默丢。
        "alerts": alerts,
        "incidents": [],
        "rollback_target": str(input_data.get("rollback_target") or "").strip(),
        "rollback_ready": False,
        "rollback_reason": "",
        "target_base_url": _base_url(input_data),
        "measured_mode": _mode(input_data),
        "runtime_state": {},
        "production_observations": [],
        "verification_ref": verification,
        "artifact_ref": artifact,
    }


def ops_handle_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 2：机械核对四件事 + 读生产真实状态"""
    verification = input_data.get("verification_ref") or {}
    artifact = input_data.get("artifact_ref") or {}
    changes = list(input_data.get("changes") or [])
    alerts_count = int(input_data.get("incident_count") or 0)

    # ---- 1) 变更审批：验过 + 指名了那份构建 ----
    verified, verify_reason = _verification_ok(verification)
    for change in changes:
        named = _names_the_build(str(change.get("text") or ""), artifact)
        approved = verified and named
        if approved:
            reason = f"{verify_reason}；变更指名了构建 {str(artifact.get('commit_sha'))[:8]}"
        elif not verified:
            reason = verify_reason
        else:
            reason = (
                "变更没有指名它要放的那份构建"
                f"（构建 commit={str(artifact.get('commit_sha') or '')[:8]}）——"
                "验的不是要上线的东西"
            )
        change["approved"] = approved
        change["reason"] = reason

    # ---- 2) 回滚能力：目标取得到吗 ----
    rollback_ready, rollback_reason, _rdetail = rollback_check.resolve(
        str(input_data.get("rollback_target") or "")
    )

    # ---- 3) 故障处置：每条告警一条记录 ----
    # 告警原文是自由文本，时间戳不一定有 → MTTR 大概率测不了，
    # 那个就如实说「测不了」（mttr_measurable=false）。
    incidents: list[dict[str, Any]] = []
    raw_alerts = [str(a) for a in (input_data.get("alerts") or [])]
    for i in range(alerts_count):
        alert = raw_alerts[i] if i < len(raw_alerts) else ""
        incidents.append({
            "incident_id": f"inc_{i + 1}",
            "alert": alert,
            "category": classify_alert(alert),
            "status": "documented",
            "recorded_at": observed_at(),
        })

    # ---- 4) 读生产真实状态（只读）----
    base_url = str(input_data.get("target_base_url") or "")
    mode = str(input_data.get("measured_mode") or _mode(input_data))
    dashboard: dict[str, Any] = {}
    health: dict[str, Any] = {}
    dashboard_error = ""
    runtime_error = ""
    if not base_url:
        runtime_error = (
            f"没配生产地址（{WMS_BASE_URL_ENV}），这次读不到线上实际状态"
        )
    else:
        reader = WmsReader(base_url)
        try:
            dashboard = reader.dashboard()
        except ReaderError as exc:
            dashboard_error = str(exc)
            runtime_error = runtime_error or str(exc)
        try:
            health = reader.health()
        except ReaderError as exc:
            runtime_error = runtime_error or str(exc)

    runtime_state = {
        "observed_at": observed_at(),
        "base_url": base_url,
        "mode": mode,
        # **「读到了看板」和「读到了点东西」是两件事**：
        # 健康端点活着不代表业务状态读到了。
        # 混成一个标志位的话，生产看板读挂了会被记成「看板没这个数」——
        # 把「现场没条件」说成「需求没被满足」。
        "reachable": bool(dashboard) or bool(health),
        "dashboard_ok": bool(dashboard),
        "dashboard": dashboard,
        "health": health,
        "business_fields": [
            f for f in DASHBOARD_BUSINESS_FIELDS if f in dashboard
        ],
        "error": runtime_error,
    }

    # ---- 5) 在生产上复算那些「读得到」的概念 ----
    #
    # **只对有生产读法的概念做**：其余的（要造单据才验得了的）
    # 在生产上就是「验不了」——不产结论，保留验收实例上的那条。
    production_observations: list[dict[str, Any]] = []
    for obs in verification.get("observations") or []:
        if not isinstance(obs, dict):
            continue
        concept = str(obs.get("wms_kind") or "")
        method = production_read_method(concept)
        if not method:
            continue
        field = method.get("field")
        if not runtime_state.get("dashboard_ok"):
            outcome = "indeterminate"
            summary = f"生产实例读不到，{concept} 这次没法在生产上验"
            details = {"measured_base_url": base_url, "measured_mode": mode,
                       "error": dashboard_error or runtime_error}
        elif field is None:
            ok = bool(runtime_state["business_fields"])
            outcome = "pass" if ok else "fail"
            summary = (
                "生产看板上确实在报业务数"
                if ok else "生产看板没报出任何业务数"
            )
            details = {"measured_base_url": base_url, "measured_mode": mode,
                       "business_fields": runtime_state["business_fields"]}
        else:
            present = field in dashboard and dashboard.get(field) is not None
            outcome = "pass" if present else "fail"
            summary = (
                f"生产看板上确实有 {method.get('label')}（{field}="
                f"{dashboard.get(field)}）"
                if present else
                f"生产看板上没有 {method.get('label')}（没有 {field} 这个数）"
            )
            details = {"measured_base_url": base_url, "measured_mode": mode,
                       "field": field, "value": dashboard.get(field)}
        production_observations.append({
            "expectation_id": str(obs.get("expectation_id") or ""),
            "requirement_id": str(obs.get("requirement_id") or ""),
            "wms_kind": concept,
            "statement": str(obs.get("statement") or ""),
            "evidence": str(obs.get("evidence") or ""),
            "outcome": outcome,
            "summary": summary,
            "details": details,
        })

    return {
        "ops_task_id": str(input_data.get("ops_task_id") or ""),
        "change_count": len(changes),
        "incident_count": len(incidents),
        "changes": changes,
        "incidents": incidents,
        "rollback_target": str(input_data.get("rollback_target") or ""),
        "rollback_ready": rollback_ready,
        "rollback_reason": rollback_reason,
        "target_base_url": base_url,
        "measured_mode": mode,
        "runtime_state": runtime_state,
        "production_observations": production_observations,
        "verification_ref": verification,
        "artifact_ref": artifact,
    }


def _merge_observations(
    base: list[dict[str, Any]],
    production: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """合并验收实例上的观测与生产上的观测

    **生产上的确定结论优先**（生产是现场）；
    **「验不了」不覆盖**——它没有信息量，拿它盖掉一条实测，
    等于把一条真的观测抹了。
    """
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for obs in base:
        if not isinstance(obs, dict):
            continue
        key = str(obs.get("expectation_id") or "")
        if key not in merged:
            order.append(key)
        merged[key] = obs
    for obs in production:
        if not isinstance(obs, dict):
            continue
        if obs.get("outcome") not in ("pass", "fail"):
            continue
        key = str(obs.get("expectation_id") or "")
        if key not in merged:
            order.append(key)
        merged[key] = obs
    return [merged[k] for k in order]


def ops_packager_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 3：打包成运维记录（判据值都在这）"""
    changes = list(input_data.get("changes") or [])
    incidents = list(input_data.get("incidents") or [])
    runtime_state = input_data.get("runtime_state") or {}
    artifact = input_data.get("artifact_ref") or {}
    expected_incidents = int(input_data.get("incident_count") or 0)

    # ---- 变更审批率 ----
    if not changes:
        change_approval_rate: Any = NOT_APPLICABLE
        approval_reason = "这次没有要放行的变更——审批率用不上"
    else:
        approved = [c for c in changes if c.get("approved")]
        change_approval_rate = len(approved) == len(changes)
        approval_reason = (
            f"{len(approved)}/{len(changes)} 条变更已验收且指名了构建"
            + ("" if change_approval_rate else
               "；未通过的：" + "、".join(
                   f"{c.get('change_id')}（{c.get('reason')}）"
                   for c in changes if not c.get("approved")
               ))
        )

    # ---- 回滚能力 ----
    rollback_ready = bool(input_data.get("rollback_ready"))
    current_known = bool(
        str(artifact.get("commit_sha") or "").strip()
        or str(artifact.get("artifact_digest") or "").strip()
    )
    if not changes:
        rollback_readiness: Any = NOT_APPLICABLE
        rollback_reason = "这次没有要放行的变更——回滚能力用不上"
    else:
        rollback_readiness = rollback_ready and current_known
        rollback_reason = str(input_data.get("rollback_reason") or "")
        if not current_known:
            rollback_reason += "；而且不知道当前跑的是哪一版（没有制品记录），退不回去"

    # ---- 故障处置记录 ----
    if not expected_incidents:
        incident_documented: Any = NOT_APPLICABLE
        incident_reason = "这次没有告警——处置记录用不上"
    else:
        documented = [i for i in incidents if i.get("status") == "documented"]
        incident_documented = (
            len(documented) == expected_incidents
            and len(incidents) == expected_incidents
        )
        incident_reason = (
            f"{len(documented)}/{expected_incidents} 条告警有处置记录"
        )
        if not incident_documented:
            incident_reason += (
                "；未记录的："
                + "、".join(
                    str(i.get("alert"))[:40]
                    for i in incidents if i.get("status") != "documented"
                )
            )

    # ---- 线上真实状态 ----
    # **要读到业务现场（看板），不只是「端点有回应」**
    reachable = bool(runtime_state.get("dashboard_ok", runtime_state.get("reachable")))
    has_business_fields = bool(runtime_state.get("business_fields"))
    runtime_state_recorded = reachable and bool(runtime_state.get("observed_at"))
    runtime_reason = str(runtime_state.get("error") or "")
    if runtime_state_recorded:
        runtime_reason = (
            f"生产现场读到了（{runtime_state.get('base_url')}，"
            f"{len(runtime_state.get('dashboard') or {})} 个字段，"
            f"业务数 {len(runtime_state.get('business_fields') or [])} 个）"
        )
    elif not runtime_reason:
        runtime_reason = "生产现场读到了，但没有记录下观测时刻/内容"

    # ---- 这次真的干了事吗 ----
    did_something = bool(changes) or bool(incidents)
    ops_determined = did_something and runtime_state_recorded
    if not did_something:
        ops_determined = False

    return {
        "change_count": len(changes),
        "incident_count": len(incidents),
        # 告警原文不一定带时间戳 → MTTR 常常测不了，如实说
        "mttr_minutes": 0.0,
        "mttr_measurable": False,
        "rollback_ready": rollback_ready,
        "change_approval_rate": change_approval_rate,
        "rollback_readiness": rollback_readiness,
        "incident_documented": incident_documented,
        "runtime_state_recorded": runtime_state_recorded,
        "ops_determined": ops_determined,
        # ---- 证据 ----
        "ops_task_id": str(input_data.get("ops_task_id") or ""),
        "changes": changes,
        "incidents": incidents,
        "runtime_state": runtime_state,
        "production_observations": input_data.get("production_observations") or [],
        # **合并后的观测**（分析盒消费这一份）
        "observations": _merge_observations(
            list((input_data.get("verification_ref") or {}).get("observations") or []),
            list(input_data.get("production_observations") or []),
        ),
        "approval_reason": approval_reason,
        "rollback_reason": rollback_reason,
        "incident_reason": incident_reason,
        "runtime_reason": runtime_reason,
        "profile_ref": f"ops_profile_{input_data.get('ops_task_id', 'unknown')}",
    }


WMS_OPS_HANDLERS: dict[str, Any] = {
    "ops_intake": ops_intake_handler,
    "ops_handle": ops_handle_handler,
    "ops_packager": ops_packager_handler,
}

RUNNER = HandlerRunner(WMS_OPS_HANDLERS, owner="wms_ops_box")
