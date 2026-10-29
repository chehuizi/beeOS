"""
boxes.wms_testing.runner - WMS 测试盒的 3 个 operation 实现

WMS 测试盒——拿开发盒的制品，用**WMS 业务场景**验它：
收货上架、拣货复核、盘点、批次效期这些真实单据跑一遍。

**这条腿现在是真的**：它连真的 WMS 实例、造真的单据、采真的实测值。

**四种结论，不是一种**（见 boxes/wms_testing/probes.py）：
pass / fail / not_applicable（没探针）/ indeterminate（判不了）。

**三种「不算验过」不许混成通过**：
- 没有探针 → 我们还没写
- 判不了 → 现场没条件
- 探针本身出错 → **履约中止**（不降级，不记成跳过）

**造数场景只跑在验证实例上**（`mode=verification`）：
往生产库里塞假单据 = 污染真实业务。生产实例上只跑只读探针，
造数场景判 indeterminate 并说明原因。
"""

from __future__ import annotations

import os
from typing import Any

from boxes.wms_testing.probes import (
    OUTCOME_FAIL,
    OUTCOME_INDETERMINATE,
    OUTCOME_NOT_APPLICABLE,
    OUTCOME_PASS,
    SCENARIOS,
    ProbeContext,
    ProbeResult,
    find_probe,
)
from boxes.wms_testing.wms_client import WmsClient, WmsError
from core.models import NOT_APPLICABLE
from runtime.runner import HandlerRunner


# 环境变量：
#   BEEOS_WMS_BASE_URL —— WMS 的 API 根（不配就全判 indeterminate）
#   BEEOS_WMS_MODE     —— `verification` 才允许造数；默认 production（只读）
WMS_BASE_URL_ENV = "BEEOS_WMS_BASE_URL"
WMS_MODE_ENV = "BEEOS_WMS_MODE"


def _wms_base_url() -> str:
    return (os.environ.get(WMS_BASE_URL_ENV) or "").strip()


def _wms_mode() -> str:
    return (os.environ.get(WMS_MODE_ENV) or "production").strip()


def _expectations_of(input_data: dict[str, Any]) -> list[dict[str, Any]]:
    """取出预期列表

    预期有两种可能的来法：
    - `expectation_ref` 是 ref:（接力来的建模盒产出）→ 里面是 expectations[]
    - 直接投的 `expectations`（手工投料时用）
    """
    ref = input_data.get("expectation_ref")
    if isinstance(ref, dict):
        rows = ref.get("expectations")
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
    rows = input_data.get("expectations")
    if isinstance(rows, list):
        return [r for r in rows if isinstance(r, dict)]
    return []


def _requirements_of(input_data: dict[str, Any]) -> list[dict[str, Any]]:
    ref = input_data.get("requirement_ref")
    if isinstance(ref, dict):
        rows = ref.get("requirements")
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
    rows = input_data.get("requirements")
    if isinstance(rows, list):
        return [r for r in rows if isinstance(r, dict)]
    return []


def _observation(
    expectation: dict[str, Any],
    outcome: str,
    summary: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """一条观测（预期 ↔ 实测的逐条对照）"""
    return {
        "expectation_id": expectation.get("expectation_id", ""),
        "requirement_id": expectation.get("requirement_id", ""),
        "wms_kind": expectation.get("wms_kind", ""),
        "statement": expectation.get("statement", ""),
        "evidence": expectation.get("evidence", ""),
        "outcome": outcome,
        "summary": summary,
        "details": details or {},
    }


def test_intake_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 1：收下测试任务，排好「谁验什么」

    这一跳只做**编排**：把每条预期归到一个探针上（或者认出来
    「没有探针」）。不跑任何场景——跑场景是 op 2 的事。

    **op 的输入 = 上一个 op 的输出**（executor 里 `op_input = dict(op_output)`），
    所以这一跳必须把需求集 / 预期 / 场景选择**显式带下去**，
    漏一个，下一跳就拿到空列表而不报错。
    """
    expectations = _expectations_of(input_data)
    requirements = _requirements_of(input_data)
    requested_scenarios = input_data.get("scenarios") or []
    if not isinstance(requested_scenarios, list):
        requested_scenarios = []

    plan: list[dict[str, Any]] = []
    unknown_scenarios: list[str] = []
    for scenario_id in requested_scenarios:
        if scenario_id not in SCENARIOS:
            unknown_scenarios.append(str(scenario_id))

    for exp in expectations:
        concept = exp.get("wms_kind") or ""
        found = find_probe(concept)
        plan.append({
            "expectation": exp,
            "concept": concept,
            "scenario_id": found[0].scenario_id if found else None,
        })

    return {
        "test_task_id": input_data.get("test_task_id", ""),
        # ---- 往下带（下一跳的输入就是这一跳的输出）----
        "requirement_ref": input_data.get("requirement_ref", {}),
        "expectation_ref": input_data.get("expectation_ref", {}),
        "requirements": requirements,
        "expectations": expectations,
        "scenarios": requested_scenarios,
        "artifact_ref": input_data.get("artifact_ref", {}),
        # ---- 编排结果 ----
        "plan": plan,
        "unknown_scenarios": unknown_scenarios,
        "case_count": len(plan),
        "requirement_count": len(requirements),
    }


def test_execute_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 2：跑场景 + 跑探针，采实测值

    **场景只准备一次**：多个预期可能落在同一个场景上，
    每个预期都重跑一遍场景 = 造一堆单据，还会让前一个预期的事实
    对不上后一个（库存已经被消耗了）。
    """
    plan = input_data.get("plan") or []
    base_url = _wms_base_url()
    mode = _wms_mode()

    observations: list[dict[str, Any]] = []
    scenario_errors: dict[str, str] = {}

    # 场景按需准备（每个场景一次）
    facts_cache: dict[str, dict[str, Any]] = {}
    prepared: set[str] = set()

    for item in plan:
        exp = item.get("expectation") or {}
        scenario_id = item.get("scenario_id")
        if not scenario_id:
            observations.append(_observation(
                exp, OUTCOME_NOT_APPLICABLE,
                f"没有针对概念 {item.get('concept')!r} 的探针——这条预期没人验",
                {"concept": item.get("concept", "")},
            ))
            continue

        scenario = SCENARIOS.get(scenario_id)
        if scenario is None:
            observations.append(_observation(
                exp, OUTCOME_NOT_APPLICABLE,
                f"场景 {scenario_id!r} 不存在",
                {"scenario_id": scenario_id},
            ))
            continue

        if not base_url:
            observations.append(_observation(
                exp, OUTCOME_INDETERMINATE,
                f"没配 WMS 地址（{WMS_BASE_URL_ENV}），这次验不了",
                {"scenario_id": scenario_id},
            ))
            continue

        if scenario.mutates and mode != "verification":
            observations.append(_observation(
                exp, OUTCOME_INDETERMINATE,
                f"场景 {scenario_id!r} 要造单据，但实例模式是 {mode!r}——"
                "只允许在验证实例上造数",
                {"scenario_id": scenario_id, "mode": mode},
            ))
            continue

        if scenario_id not in prepared:
            try:
                ctx = ProbeContext(
                    client=WmsClient(base_url),
                    mode=mode,
                    requirements={
                        r.get("requirement_id", ""): r
                        for r in input_data.get("requirements") or []
                    },
                    # **前端产物**：开发盒交付的那一份 dist。
                    # 前端探针验的就是它——跟后端验「部署到验证实例的那份 jar」
                    # 是同一个道理：**测的必须是交付物本身**。
                    frontend_dist_path=str(input_data.get("frontend_dist_path") or ""),
                    frontend_expected=bool(input_data.get("frontend_expected")),
                )
                facts_cache[scenario_id] = scenario.prepare(ctx)
            except WmsError as exc:
                # **探针跑不起来 = 履约中止**，不是记一句「跳过」。
                # 降级成「跳过」的话，WMS 挂了会显示成「全都验过了」。
                raise
            prepared.add(scenario_id)

        facts = facts_cache.get(scenario_id) or {}
        probe = next(
            (p for p in scenario.probes if p.concept == item.get("concept")),
            None,
        )
        if probe is None:
            observations.append(_observation(
                exp, OUTCOME_NOT_APPLICABLE,
                f"场景 {scenario_id!r} 里没有概念 {item.get('concept')!r} 的探针",
                {"scenario_id": scenario_id},
            ))
            continue

        result: ProbeResult = probe.check(ctx, facts)
        observations.append(_observation(
            exp, result.outcome, result.summary, result.details,
        ))

    # ---- 每条观测都带上「这次测的是哪个实例」----
    #
    # 没有这一句，报告上只写着「临期 SKU 数是 0」，读者分不清
    # 「我在验证实例上量的」和「我在生产上量的」——
    # 而运维盒（把线上真实状态回写档案）没实现时，
    # 这个信息在链上就没有别的地方可去了。
    #
    # 尤其在 mode=production 上：观测会判 indeterminate
    # （造数场景只许在验证实例上跑），**这时候必须让人看见
    # 「我是去生产量的、所以量不了」**，而不是只看到一个空结论。
    for obs in observations:
        details = obs.setdefault("details", {})
        details["measured_base_url"] = base_url
        details["measured_mode"] = mode
        # **这次测的是哪份构建**——
        # 制品摘要来自开发盒的产出（它自己说是从哪个提交编出来的）。
        # 不写这一句，读者看到「临期 SKU 数是 1」时，
        # 分不清测的是哪份代码——而「测的是哪份代码」恰恰是
        # 「这次交付到底算不算数」的前一半。
        artifact = input_data.get("artifact_ref") or {}
        if isinstance(artifact, dict) and artifact.get("artifact_digest"):
            details["artifact_digest"] = artifact.get("artifact_digest")
            details["artifact_commit_count"] = artifact.get("commit_count")
        details["artifact_present"] = bool(isinstance(artifact, dict) and artifact)

    return {
        "test_task_id": input_data.get("test_task_id", ""),
        "requirements": input_data.get("requirements", []),
        "expectations": input_data.get("expectations", []),
        "scenarios": input_data.get("scenarios", []),
        "unknown_scenarios": input_data.get("unknown_scenarios", []),
        "artifact_ref": input_data.get("artifact_ref", {}),
        "requirement_ref": input_data.get("requirement_ref", {}),
        "expectation_ref": input_data.get("expectation_ref", {}),
        "observations": observations,
        "case_count": len(observations),
        "scenario_errors": scenario_errors,
        "scenario_facts": [
            {
                "scenario_id": sid,
                "sku_code": (f.get("sku") or {}).get("code"),
                "batches": [
                    {
                        "batch_no": b.get("batch_no"),
                        "expire_date": b.get("expire_date"),
                        "produced_date": b.get("produced_date"),
                        "qty": b.get("qty"),
                    }
                    for b in f.get("batches") or []
                ],
                "allocated_batch_no": (f.get("allocated") or {}).get("batch_no"),
                "allocated_qty": (f.get("allocated") or {}).get("qty"),
            }
            for sid, f in facts_cache.items()
        ],
    }


def test_packager_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 3：打包成测试报告

    **五种结论分开数**：pass / fail / not_applicable / indeterminate。
    把后两种混进通过率里，「什么都没验」就会显示成 100% 通过——
    那是这个项目里最坏的一种输出。
    """
    observations = input_data.get("observations") or []
    requirements = input_data.get("requirements") or []

    passed = [o for o in observations if o.get("outcome") == OUTCOME_PASS]
    failed = [o for o in observations if o.get("outcome") == OUTCOME_FAIL]
    n_a = [o for o in observations if o.get("outcome") == OUTCOME_NOT_APPLICABLE]
    unknown = [o for o in observations if o.get("outcome") == OUTCOME_INDETERMINATE]

    determined = len(passed) + len(failed)
    # 通过率**只在有确定结论的那些上算**。
    # 分母 0 → n/a（不是 100%）：一条都没验出来不是「全都没问题」。
    if determined:
        case_pass_rate = round(100.0 * len(passed) / determined, 2)
    else:
        case_pass_rate = NOT_APPLICABLE

    # 每条需求都拿到了处置吗？（处置 = 四种结论之一）
    disposition_by_req: dict[str, set[str]] = {}
    for obs in observations:
        rid = obs.get("requirement_id") or ""
        disposition_by_req.setdefault(rid, set()).add(obs.get("outcome", ""))
    uncovered_requirements = [
        r.get("requirement_id", "") for r in requirements
        if not disposition_by_req.get(r.get("requirement_id", ""))
    ]

    # **每条观测都得说得出「测的是哪份构建」**——
    # 制品摘要是开发盒交付的那份构建的标识。
    # 少一条无主的观测，这次验证就有一截不知道在验什么。
    artifact = input_data.get("artifact_ref") or {}
    artifact_digest = (
        artifact.get("artifact_digest") if isinstance(artifact, dict) else ""
    ) or ""
    observations_all_identified = bool(observations) and all(
        (o.get("details") or {}).get("artifact_digest") == artifact_digest
        and bool((o.get("details") or {}).get("artifact_digest"))
        for o in observations
    )

    return {
        "package_id": f"test_report_{input_data.get('test_task_id', 'unknown')}",
        "box_id": "wms_testing_box",
        "test_task_id": input_data.get("test_task_id", ""),
        # ---- 判据读的量 ----
        "scenario_count": len(SCENARIOS),
        "case_count": len(observations),
        "case_pass_rate": case_pass_rate,
        "open_defect_count": len(failed),
        "requirement_case_coverage": not uncovered_requirements,
        "scenario_coverage": not input_data.get("unknown_scenarios"),
        "no_blocking_defect": not failed,
        # **至少验出了点东西**——
        # 否则「一条都没验出来」会显示成「3 条通过 + 1 条用不上」。
        "verification_determined": determined > 0,
        # 每条观测都带着「测的是哪份构建」
        "artifact_identified": observations_all_identified,
        # ---- 证据 ----
        "observations": observations,
        "passed_count": len(passed),
        "failed_count": len(failed),
        "not_applicable_count": len(n_a),
        "indeterminate_count": len(unknown),
        "failed_expectations": [
            {
                "expectation_id": o.get("expectation_id"),
                "requirement_id": o.get("requirement_id"),
                "wms_kind": o.get("wms_kind"),
                "summary": o.get("summary"),
                "details": o.get("details"),
            }
            for o in failed
        ],
        "not_applicable_expectations": [
            {
                "expectation_id": o.get("expectation_id"),
                "requirement_id": o.get("requirement_id"),
                "wms_kind": o.get("wms_kind"),
                "summary": o.get("summary"),
            }
            for o in n_a
        ],
        "indeterminate_expectations": [
            {
                "expectation_id": o.get("expectation_id"),
                "requirement_id": o.get("requirement_id"),
                "wms_kind": o.get("wms_kind"),
                "summary": o.get("summary"),
            }
            for o in unknown
        ],
        "uncovered_requirements": uncovered_requirements,
        "unknown_scenarios": input_data.get("unknown_scenarios", []),
        "scenario_facts": input_data.get("scenario_facts", {}),
        "artifact_ref": input_data.get("artifact_ref", {}),
    }


WMS_TESTING_HANDLERS: dict[str, Any] = {
    "test_intake": test_intake_handler,
    "test_execute": test_execute_handler,
    "test_packager": test_packager_handler,
}

RUNNER = HandlerRunner(WMS_TESTING_HANDLERS, owner="wms_testing_box")
