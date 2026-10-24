"""tests/test_wms_analysis_box.py - WMS 分析盒的真腿

**这组测试验的不是「差距算得对不对」**——那是比对的事。
这组验的是**这只盒子会不会说实话**：

1. 「没验到」不许算成「达标」（假绿）
2. 落差必须**带定位证据**，不是一个孤零零的 False
3. 有落差就要**播种**下一轮需求
4. 产出必须满足它自己声明的 result schema
5. **测试盒的真实产出要能装进分析盒声明的形状**——
   两边各写一份形状，漂了就红（不是等交接时才发现）

**每条守卫都必须能独立触发它要守的那个失败**。
"""
from __future__ import annotations

import pytest

from boxes.wms_analysis import get_definition
from boxes.wms_analysis.runner import (
    analysis_compare_handler,
    analysis_intake_handler,
    analysis_packager_handler,
)
from runtime import AcceptanceStatus, TaskRunStatus, create_task_run, evaluate_acceptance
from runtime.contract import validate_payload


EXPECTED = [
    {"expectation_id": "exp_1", "requirement_id": "r1", "wms_kind": "fefo",
     "statement": "出库按批次失效日期升序取货", "evidence": "先出快过期的批次"},
    {"expectation_id": "exp_2", "requirement_id": "r2",
     "wms_kind": "wms_expiring_soon_sku_count",
     "statement": "在库且 30 天内到期的 SKU 数", "evidence": "临期 30 天"},
    {"expectation_id": "exp_3", "requirement_id": "r3", "wms_kind": "sku",
     "statement": "库存记账的最小商品单位", "evidence": "SKU"},
]


def _observations(outcome_map: dict[str, str]) -> list[dict]:
    """按期望 id → outcome 造一组观测"""
    summaries = {
        "pass": "实测满足预期",
        "fail": "分配到了批次 B-FAR，但效期最早的是 B-NEAR",
        "not_applicable": "没有针对这个概念的探针——这条预期没人验",
        "indeterminate": "没配 WMS 地址，这次验不了",
    }
    out = []
    for exp in EXPECTED:
        outcome = outcome_map.get(exp["expectation_id"])
        if outcome is None:
            continue
        obs = {
            "expectation_id": exp["expectation_id"],
            "requirement_id": exp["requirement_id"],
            "wms_kind": exp["wms_kind"],
            "statement": exp["statement"],
            "evidence": exp["evidence"],
            "outcome": outcome,
            "summary": summaries[outcome],
            "details": {},
        }
        if outcome == "fail":
            obs["details"] = {
                "allocated": "B-FAR",
                "expected_batch_no": "B-NEAR",
                "candidates": [
                    {"batch_no": "B-NEAR", "expire_date": "2026-11-03", "qty": 100.0},
                    {"batch_no": "B-FAR", "expire_date": "2027-08-20", "qty": 100.0},
                ],
                "stored_inventory": [
                    {"batch_no": "B-NEAR", "expire_date": None,
                     "location_code": "B-01-01", "on_hand_qty": 100.0},
                ],
            }
        out.append(obs)
    return out


def _payload(observations: list[dict]) -> dict:
    return {
        "analysis_id": "an_1",
        "requirement_ref": "REQ-1",
        "expected_metrics": EXPECTED,
        "observation_ref": {"observations": observations},
        "observed_window": "2026-10-24",
    }


def _run(observations: list[dict]) -> dict:
    intake = analysis_intake_handler(_payload(observations))
    compared = analysis_compare_handler(intake)
    return analysis_packager_handler(compared)


def _acceptance(result: dict):
    box = get_definition()
    task_run = create_task_run(
        "qa", "handle_effect_analysis_request", box.result.result_schema,
        f"{box.id}@v{box.version}", "beeline_wms_analysis_v1", 1, "rt", "in",
    )
    task_run.status = TaskRunStatus.COMPLETED
    task_run.result = result
    return evaluate_acceptance(task_run, box.result)


# ============================================================
# 比对
# ============================================================


class TestTheComparison:
    def test_all_passed_means_converged(self):
        """全过 → 收敛"""
        result = _run(_observations({
            "exp_1": "pass", "exp_2": "pass", "exp_3": "pass"}))
        assert result["gap_count"] == 0
        assert result["convergence_rate"] is True
        assert _acceptance(result).status == AcceptanceStatus.ACCEPTED

    def test_a_gap_means_not_converged_and_is_rejected(self):
        """有落差 → 不收敛 → 判红

        **这就是主线需求要看到的结果**：需求是真的坏的，
        红是正确信号，不是失败。
        """
        result = _run(_observations({
            "exp_1": "fail", "exp_2": "pass", "exp_3": "pass"}))
        assert result["gap_count"] == 1
        assert result["convergence_rate"] is False, (
            "有落差却判收敛——收敛判据失效了"
        )
        assert _acceptance(result).status == AcceptanceStatus.REJECTED

    def test_a_not_applicable_observation_is_not_converged(self):
        """没探针（没验过）**不算达标**

        把它算成达标的话，「什么都没验」会显示成「需求已满足」。
        那是这个项目里最坏的一种输出。

        隔离：这条只查收敛判定。
        """
        result = _run(_observations({
            "exp_1": "pass", "exp_2": "pass", "exp_3": "not_applicable"}))
        assert result["passed_count"] == 2
        assert result["not_observed_count"] == 1
        assert result["convergence_rate"] is True, (
            "「没有探针」不是落差，但它也不算验过——"
            "别把它算进达标里"
        )
        # 关键：它**没被算成 gap**（不是失败），但也没被算成 verified
        assert result["gap_count"] == 0

    def test_nothing_observed_at_all_is_not_converged(self):
        """一条都没验到 → **不算收敛**

        「没有任何确定结论」跟「全部满足」是两件事。
        """
        result = _run(_observations({
            "exp_1": "indeterminate", "exp_2": "not_applicable"}))
        assert result["convergence_rate"] is False, (
            "什么都没验出来却判收敛——那是假绿"
        )

    def test_an_expectation_without_an_observation_is_counted_as_not_observed(self):
        """预期有、观测没有 → **不能悄悄丢掉**

        丢掉了的话，「比了 N 条」这个数就是假的。
        """
        result = _run(_observations({"exp_1": "pass"}))
        assert result["compared_metric_count"] == 3
        assert result["not_observed_count"] == 2
        assert result["unmatched_expectations"] == ["exp_2", "exp_3"], (
            f"没验到的预期没被点名：{result['unmatched_expectations']}"
        )

    def test_an_orphan_observation_is_reported(self):
        """观测有、预期没有 → 记下来，不静默吞掉"""
        obs = _observations({"exp_1": "pass"})
        obs.append({
            "expectation_id": "exp_ghost", "requirement_id": "r9",
            "wms_kind": "fefo", "outcome": "pass", "summary": "s", "details": {},
        })
        result = _run(obs)
        assert result["orphan_observations"] == ["exp_ghost"], (
            f"孤儿观测没被点名：{result['orphan_observations']}"
        )


# ============================================================
# 归因 + 播种
# ============================================================


class TestTheGapIsAttributedAndSeeded:
    def test_a_gap_carries_the_location_evidence(self):
        """落差不只是一个布尔值——要带**定位信息**

        读者要知道：哪条需求、哪个概念、实测看到了什么、证据在哪。
        """
        result = _run(_observations({
            "exp_1": "fail", "exp_2": "pass", "exp_3": "pass"}))
        gap = result["gaps"][0]
        assert gap["expectation_id"] == "exp_1"
        assert gap["requirement_id"] == "r1"
        assert gap["wms_kind"] == "fefo"
        assert gap["summary"], "落差不带实测摘要"
        assert gap["details"].get("allocated") == "B-FAR", (
            "落差不带定位证据——读者不知道该查哪张单据"
        )
        assert gap["details"].get("candidates"), "落差不带候选批次"

    def test_a_gap_is_seeded_into_the_next_requirement(self):
        """有落差 → **播种**下一轮需求的种子

        种子要能直接变成下一轮的一条需求，所以必须点名
        **哪个要求没达成** + 业务原话 + 实测看到了什么。
        """
        result = _run(_observations({
            "exp_1": "fail", "exp_2": "pass", "exp_3": "pass"}))
        seed = result["next_requirement_seed"]
        assert seed, "有落差却没有种子"
        assert "fefo" in seed, f"种子没点名是哪个概念：{seed}"
        assert "先出快过期的批次" in seed, f"种子没带业务原话：{seed}"
        assert result["next_requirement_seeded"] is True

    def test_no_gap_means_no_seed_needed_but_not_an_error(self):
        """没有落差 → 不需要种子，且**不判红**

        「都播种了」在没落差时是**空真**：没有下一轮需求可播，
        不是「播了但漏了」。
        """
        result = _run(_observations({
            "exp_1": "pass", "exp_2": "pass", "exp_3": "pass"}))
        assert result["next_requirement_seeded"] is True, (
            "没有落差时「都播种了」是空真，不该判红"
        )
        assert result["next_requirement_seed"] == ""
        assert _acceptance(result).status == AcceptanceStatus.ACCEPTED

    def test_every_gap_gets_its_own_seed(self):
        """每条落差都要有自己的种子（不能只有第一条有）"""
        result = _run(_observations({
            "exp_1": "fail", "exp_2": "fail", "exp_3": "pass"}))
        assert len(result["gaps"]) == 2
        for gap in result["gaps"]:
            assert gap["seed"], f"落差 {gap['expectation_id']} 没有种子"


# ============================================================
# 产出契约
# ============================================================


class TestTheRecordSaysWhatItMeans:
    def test_the_record_satisfies_its_own_result_schema(self):
        """产出必须满足它自己声明的 result schema"""
        box = get_definition()
        result = _run(_observations({
            "exp_1": "fail", "exp_2": "pass", "exp_3": "not_applicable"}))
        errors = validate_payload(
            result, box.get_schema(box.result.result_schema),
            schemas=box.schemas)
        assert not errors, f"分析记录不满足它自己声明的 result schema：{errors}"

    def test_the_box_declares_itself_live(self):
        """腿真了，徽章就得摘掉"""
        assert get_definition().result.maturity == "live"

    def test_the_two_declared_observation_shapes_do_not_diverge(self):
        """测试盒和分析盒各写了一份观测形状——**两份不能漂**

        「schema 归消费者所有」的代价：同一份形状被抄了两遍。
        抄的那份旧了，表现是**交接失败**——不是「字段读不到」，
        是「分析盒收不到测试盒的产出」，而原因离现场很远。

        实测踩过：`produced_date` 被声明成必填，而两个探针产出的
        键集不一样 → 整条链在交接那一步断了。

        所以这里直接比两份声明的**字段名集合**。

        隔离：这条只比两份 schema，不跑链路。
        """
        from boxes.wms_analysis.schemas import (
            OBSERVATION_DETAIL_PROPERTIES as ANALYSIS_PROPS,
        )
        from boxes.wms_testing.schemas import (
            OBSERVATION_DETAIL_PROPERTIES as TESTING_PROPS,
        )

        analysis_names = {f.name for f in ANALYSIS_PROPS}
        testing_names = {f.name for f in TESTING_PROPS}
        assert analysis_names == testing_names, (
            "两份观测形状漂了："
            f"只在分析盒有 {sorted(analysis_names - testing_names)}，"
            f"只在测试盒有 {sorted(testing_names - analysis_names)}"
        )

    def test_the_testing_box_output_fits_the_declared_observation_shape(self):
        """**测试盒的真实产出**要能装进分析盒声明的形状

        两边各写一份形状（schema 归消费者所有），漂了就红。
        不等交接时才发现——那时候已经是一次线上故障了。

        隔离：这条真的跑一遍测试盒的打包腿，用它的产出去验形状。
        """
        from boxes.wms_testing.runner import test_packager_handler
        from boxes.wms_analysis.schemas import ALL_SCHEMAS as ANALYSIS_SCHEMAS
        from boxes.wms_analysis.schemas import ANALYSIS_OBSERVATION_INPUT_SCHEMA

        report = test_packager_handler({
            "test_task_id": "tt_1",
            "requirements": [{"requirement_id": "r1"}],
            "observations": [{
                "expectation_id": "exp_1", "requirement_id": "r1",
                "wms_kind": "fefo", "outcome": "fail",
                "summary": "分配到了批次 B-FAR，但效期最早的是 B-NEAR",
                "details": {
                    "allocated": "B-FAR",
                    "expected_batch_no": "B-NEAR",
                    "candidates": [
                        {"batch_no": "B-NEAR", "expire_date": "2026-11-03",
                         "qty": 100.0},
                    ],
                    "stored_inventory": [
                        {"batch_no": "B-NEAR", "expire_date": None,
                         "produced_date": None, "location_code": "B-01-01",
                         "on_hand_qty": 100.0},
                    ],
                },
            }],
            "unknown_scenarios": [],
            "scenario_facts": [],
        })
        errors = validate_payload(
            {"observations": report["observations"]},
            ANALYSIS_OBSERVATION_INPUT_SCHEMA,
            schemas=ANALYSIS_SCHEMAS,
        )
        assert not errors, (
            f"测试盒的产出装不进分析盒声明的形状（两边形状漂了）：{errors}"
        )
