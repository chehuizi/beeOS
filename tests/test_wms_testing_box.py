"""tests/test_wms_testing_box.py - WMS 测试盒的真腿

**这组测试验的不是「WMS 对不对」**——那是探针去活实例上验的事。
这组验的是**这只盒子会不会说实话**：

1. 「没验到」不许显示成「验过了」（假绿）
2. 「没有探针」和「现场判不了」不许混成「通过」
3. 红灯得带**定位信息**，不是一个孤零零的 False
4. 判据的**量纲要跟指标对得上**（33.33% 不算 99%）
5. 探针跑不起来 → **中止**，不降级
6. 造数场景只许跑在验证实例上

**每条守卫都必须能独立触发它要守的那个失败**：
别的闸先拦下来 = 这条守卫不承重。
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from boxes.wms_testing import get_definition
from boxes.wms_testing.probes import (
    OUTCOME_FAIL,
    OUTCOME_INDETERMINATE,
    OUTCOME_NOT_APPLICABLE,
    OUTCOME_PASS,
    ProbeContext,
    _check_expiring_sku_count,
    _check_fefo,
    _check_lot_expiry,
    find_probe,
)
# **别名**：这三个 handler 的名字是 test_ 开头的，直接 import 进来
# pytest 会把它们当成测试函数收集（然后报「fixture 'input_data' not found」）。
# 这不是小事——它们**一次都没跑过**，而源码里看着好好的。
from boxes.wms_testing.runner import (
    test_execute_handler as execute_handler,
    test_intake_handler as intake_handler,
    test_packager_handler as packager_handler,
)
from boxes.wms_testing.wms_client import WmsError
from runtime import AcceptanceStatus, TaskRunStatus, create_task_run, evaluate_acceptance
from runtime.contract import validate_payload


TODAY = date(2026, 10, 24)


def _expectations() -> list[dict]:
    return [
        {"expectation_id": "exp_1", "requirement_id": "r1", "wms_kind": "fefo",
         "statement": "出库按批次失效日期升序取货", "evidence": "先出快过期的"},
        {"expectation_id": "exp_2", "requirement_id": "r2", "wms_kind": "lot",
         "statement": "批次（同 SKU 下按生产日期区分的一批货）", "evidence": "批次"},
        {"expectation_id": "exp_3", "requirement_id": "r3",
         "wms_kind": "wms_expiring_soon_sku_count",
         "statement": "在库且 30 天内到期的 SKU 数", "evidence": "临期 30 天"},
        {"expectation_id": "exp_4", "requirement_id": "r4",
         "wms_kind": "sku", "statement": "库存记账的最小商品单位", "evidence": "SKU"},
    ]


def _requirements() -> list[dict]:
    return [{"requirement_id": f"r{i}", "description": "d", "requirement_type": "object"}
            for i in range(1, 5)]


def _payload(**overrides) -> dict:
    payload = {
        "test_task_id": "tt_1",
        "artifact_ref": {"commit_count": 1, "artifact_digest": "d"},
        "requirement_ref": {"set_id": "s1", "requirements": _requirements()},
        "expectation_ref": {"expectations": _expectations()},
        "scenarios": ["batch_expiry_allocation"],
    }
    payload.update(overrides)
    return payload


def _facts(*, expiry_lost: bool, allocated_batch: str) -> dict:
    """手造一份场景事实（不用连 WMS）

    expiry_lost=True → 上架后效期没了（**WMS 现在的真实状态**）
    expiry_lost=False → 效期还在（修好之后的样子）
    """
    near = (TODAY + timedelta(days=10)).isoformat()
    far = (TODAY + timedelta(days=300)).isoformat()
    batches = [
        {"batch_no": "B-NEAR", "expire_date": near, "produced_date": "2026-01-01",
         "qty": 100.0, "receipt_expire_date": near},
        {"batch_no": "B-FAR", "expire_date": far, "produced_date": "2026-01-01",
         "qty": 100.0, "receipt_expire_date": far},
    ]
    return {
        "sku": {"id": 99, "code": "PROBE-FEFO-TEST"},
        "batches": batches,
        "allocated": {"batch_no": allocated_batch, "qty": 100.0,
                      "location_code": "B-01-01"},
        "inventory_rows": [
            # 效期还在的话，**每个批次存自己的效期**（不是同一个值）
            {"batchNo": "B-NEAR", "expireDate": None if expiry_lost else near,
             "producedDate": None, "locationCode": "B-01-01", "onHandQty": 100.0},
            {"batchNo": "B-FAR", "expireDate": None if expiry_lost else far,
             "producedDate": None, "locationCode": "B-01-01", "onHandQty": 100.0},
        ],
        "dashboard": {"expiringSoonSkuCount": 0},
        "today": TODAY.isoformat(),
        "outbound_id": 7,
    }


def _ctx() -> ProbeContext:
    return ProbeContext(client=None, mode="verification", today=TODAY)


# ============================================================
# 判据：量纲 / 假绿 / 红灯带定位
# ============================================================


class TestTheCriteriaMeanWhatTheySay:
    def _acceptance(self, result: dict):
        box = get_definition()
        task_run = create_task_run(
            "qa", "handle_testing_request", box.result.result_schema,
            f"{box.id}@v{box.version}", "beeline_wms_testing_v1", 1, "rt", "in",
        )
        task_run.status = TaskRunStatus.COMPLETED
        task_run.result = result
        return evaluate_acceptance(task_run, box.result)

    def _report(self, **overrides) -> dict:
        report = {
            "scenario_count": 1,
            "case_count": 4,
            "case_pass_rate": 100.0,
            "open_defect_count": 0,
            "requirement_case_coverage": True,
            "scenario_coverage": True,
            "no_blocking_defect": True,
            "verification_determined": True,
            # 每条观测都得说得出「测的是哪份构建」
            "artifact_identified": True,
        }
        report.update(overrides)
        return report

    def test_a_low_pass_rate_is_rejected(self):
        """33.33% 的通过率必须判红

        **这条守卫盯的是「量纲」**：case_pass_rate 是 0-100 的百分数，
        阈值必须跟它对得上。写成 `gte 0.99` 的话，33.33 也算通过——
        真跑一次就看见了（实测 6 条里过 2 条，判据却显示 passed）。
        """
        result = self._acceptance(self._report(case_pass_rate=33.33))
        assert result.status == AcceptanceStatus.REJECTED, (
            "33.33% 的通过率被判成通过了——判据阈值跟指标的量纲对不上"
        )

    def test_a_full_pass_rate_is_accepted(self):
        """100% 且没有失败 → 判绿（**别把判据写成永远红**）"""
        result = self._acceptance(self._report())
        assert result.status == AcceptanceStatus.ACCEPTED

    def test_nothing_verified_is_not_a_green_run(self):
        """一条都没验出来 → **必须判红**

        没有这条判据的话，「什么都没验」会显示成
        「几条通过 + 几条用不上」——读起来像「基本没问题」。
        那是假信号。

        隔离：这条只查 `verification_determined` 这条判据。
        """
        result = self._acceptance(self._report(
            case_pass_rate="n/a", verification_determined=False))
        assert result.status == AcceptanceStatus.REJECTED, (
            "什么都没验出来却判绿——那是假信号"
        )

    def test_a_failed_expectation_turns_the_defect_check_red(self):
        """有失败预期 → `no_blocking_defect` 红"""
        result = self._acceptance(self._report(
            case_pass_rate=50.0, open_defect_count=1, no_blocking_defect=False))
        assert result.status == AcceptanceStatus.REJECTED

    def test_the_box_declares_itself_live(self):
        """腿是真的了，徽章就得摘掉

        `maturity="skeleton"` 让看板显式标「未实现」。
        腿真了还挂着「未实现」= 看板骗人。
        """
        assert get_definition().result.maturity == "live"

    def test_the_report_satisfies_its_own_result_schema(self):
        """产出必须满足它自己声明的 result schema

        「声明了却拿不到」的同族病。判据字段没写进 schema 的话，
        照 schema 生成的消费方永远拿不到它们。
        """
        box = get_definition()
        report = packager_handler({
            "test_task_id": "tt_1",
            "requirements": _requirements(),
            "observations": [
                {"expectation_id": "exp_1", "requirement_id": "r1",
                 "wms_kind": "fefo", "outcome": OUTCOME_PASS, "summary": "ok",
                 "details": {}},
                {"expectation_id": "exp_9", "requirement_id": "r9",
                 "wms_kind": "sku", "outcome": OUTCOME_NOT_APPLICABLE,
                 "summary": "没探针", "details": {}},
            ],
            "unknown_scenarios": [],
            "scenario_facts": [],
        })
        errors = validate_payload(
            report, box.get_schema(box.result.result_schema),
            schemas=box.schemas)
        assert not errors, f"测试报告不满足它自己声明的 result schema：{errors}"

    def test_observations_without_a_build_digest_are_not_an_identified_run(self):
        """观测没带「测的是哪份构建」→ 判红

        「临期 SKU 数是 1」这句话若无主（不知道测的是哪份代码），
        这次验证就有一截不知道在验什么——它不该显示成「验过了」。
        """
        report = packager_handler({
            "test_task_id": "tt_1",
            "requirements": _requirements(),
            "observations": [
                {"expectation_id": "exp_1", "requirement_id": "r1",
                 "wms_kind": "fefo", "outcome": OUTCOME_PASS, "summary": "ok",
                 "details": {}},   # ← 没有 artifact_digest
            ],
            "artifact_ref": {"artifact_digest": "sha256:abc"},
            "unknown_scenarios": [],
            "scenario_facts": [],
        })
        assert report["artifact_identified"] is False
        result = self._acceptance(report)
        assert result.status == AcceptanceStatus.REJECTED

    def test_observations_carrying_the_build_digest_are_identified(self):
        """带上了制品摘要 → 这次验算有主"""
        report = packager_handler({
            "test_task_id": "tt_1",
            "requirements": _requirements(),
            "observations": [
                {"expectation_id": "exp_1", "requirement_id": "r1",
                 "wms_kind": "fefo", "outcome": OUTCOME_PASS, "summary": "ok",
                 "details": {"artifact_digest": "sha256:abc"}},
            ],
            "artifact_ref": {"artifact_digest": "sha256:abc"},
            "unknown_scenarios": [],
            "scenario_facts": [],
        })
        assert report["artifact_identified"] is True


# ============================================================
# 三种「不算验过」不许混成通过
# ============================================================


class TestTheThreeKindsOfNotVerified:
    def test_a_concept_without_a_probe_is_not_applicable(self, monkeypatch):
        """没有探针的概念 → `not_applicable`，**不是 pass**

        「没写探针」是**我们还没写**，不是「这条需求不重要」。
        混进通过里就是假绿。

        隔离：这条走的是「没有 WMS 地址」的路径（探针根本不会被找到），
        所以它验的是**分流逻辑**，不依赖任何 WMS 行为。
        """
        monkeypatch.delenv("BEEOS_WMS_BASE_URL", raising=False)
        monkeypatch.setenv("BEEOS_WMS_MODE", "verification")
        intake = intake_handler(_payload())
        executed = execute_handler(intake)
        by_concept = {o["wms_kind"]: o["outcome"] for o in executed["observations"]}
        assert by_concept["sku"] == OUTCOME_NOT_APPLICABLE, (
            f"没探针的概念被判成了 {by_concept['sku']!r}——那是假绿"
        )
        assert by_concept["fefo"] == OUTCOME_INDETERMINATE, (
            "fefo 有探针，不该是 n/a（没配地址 → 判不了，不是没探针）"
        )

    def test_a_missing_wms_url_is_indeterminate_not_a_pass(self, monkeypatch):
        """没配 WMS 地址 → `indeterminate`（现场判不了），不是 pass"""
        monkeypatch.delenv("BEEOS_WMS_BASE_URL", raising=False)
        monkeypatch.setenv("BEEOS_WMS_MODE", "verification")
        intake = intake_handler(_payload())
        executed = execute_handler(intake)
        assert all(o["outcome"] == OUTCOME_INDETERMINATE
                   for o in executed["observations"]
                   if o["wms_kind"] != "sku"), (
            "没配 WMS 地址时不该有通过项——现场判不了就是判不了"
        )

    def test_a_mutating_scenario_is_refused_on_a_production_instance(self, monkeypatch):
        """生产实例上**不许造单据**

        往真实业务里塞假单据 = 污染生产数据。
        挡不住就只能靠人记得，而人不会记得。

        隔离：这条只查模式判断，不碰 WMS。
        """
        monkeypatch.setenv("BEEOS_WMS_BASE_URL", "http://127.0.0.1:8081/api/v1")
        monkeypatch.setenv("BEEOS_WMS_MODE", "production")
        intake = intake_handler(_payload())
        executed = execute_handler(intake)
        fefo = [o for o in executed["observations"] if o["wms_kind"] == "fefo"]
        assert fefo and fefo[0]["outcome"] == OUTCOME_INDETERMINATE, (
            "生产实例上竟然允许造数场景"
        )
        assert "验证实例" in fefo[0]["summary"], (
            f"没说明为什么判不了：{fefo[0]['summary']}"
        )

    def test_every_observation_says_which_instance_it_measured(self, monkeypatch):
        """每条观测都要说清「我测的是哪个实例」

        没有这一句，报告上只写着「临期 SKU 数是 0」——
        读者分不清「我在验证实例上量的」和「我在生产上量的」。

        运维盒（把线上真实状态回写档案）没实现时，这个信息在链上
        没有别的地方可去 → 「生产上满没满足」连「答不了」都看不见。
        """
        monkeypatch.setenv("BEEOS_WMS_BASE_URL", "http://127.0.0.1:8081/api/v1")
        monkeypatch.setenv("BEEOS_WMS_MODE", "verification")
        intake = intake_handler(_payload())
        executed = execute_handler(intake)
        assert executed["observations"]
        for o in executed["observations"]:
            d = o.get("details") or {}
            assert d.get("measured_base_url") == "http://127.0.0.1:8081/api/v1", (
                f"这条观测没说自己测的是哪个实例：{o.get('summary')}"
            )
            assert d.get("measured_mode") == "verification", (
                f"这条观测没说自己测的是哪个环境：{o.get('summary')}"
            )

    def test_an_indeterminate_observation_also_says_where_it_tried(self, monkeypatch):
        """判不了的时候更要说明「我去哪了、为什么判不了」

        生产实例上，造数场景被拒 → 观测是 indeterminate。
        这时候读者最需要知道的就是「我去的是生产，所以量不了」，
        而不是只看到一个空结论。
        """
        monkeypatch.setenv("BEEOS_WMS_BASE_URL", "http://prod.example/wms/api/v1")
        monkeypatch.setenv("BEEOS_WMS_MODE", "production")
        intake = intake_handler(_payload())
        executed = execute_handler(intake)
        fefo = [o for o in executed["observations"] if o["wms_kind"] == "fefo"]
        assert fefo and fefo[0]["outcome"] == OUTCOME_INDETERMINATE
        d = fefo[0].get("details") or {}
        assert d.get("measured_mode") == "production"
        assert d.get("measured_base_url") == "http://prod.example/wms/api/v1"

    def test_a_wms_outage_aborts_the_run(self, monkeypatch):
        """WMS 连不上 → **中止履约**，不是记一句跳过

        降级成「跳过」的话，WMS 挂了会显示成「全都验过了」。
        """
        monkeypatch.setenv("BEEOS_WMS_BASE_URL", "http://127.0.0.1:9/api/v1")
        monkeypatch.setenv("BEEOS_WMS_MODE", "verification")
        intake = intake_handler(_payload())
        with pytest.raises(WmsError):
            execute_handler(intake)

    def test_the_pass_rate_ignores_not_applicable_and_indeterminate(self):
        """通过率**只在有确定结论的那些上算**

        把 n/a 和 indeterminate 混进分母，「什么都没验」就会显示成
        低通过率（像是「验了但没过」）；混进分子，就是 100% 通过。
        两种都骗人。正确做法是：**不算**，另设一条判据盯「有没有验到东西」。

        隔离：这条只查打包腿的算法。
        """
        report = packager_handler({
            "test_task_id": "tt_1",
            "requirements": _requirements(),
            "observations": [
                {"expectation_id": "e1", "requirement_id": "r1", "wms_kind": "fefo",
                 "outcome": OUTCOME_PASS, "summary": "ok", "details": {}},
                {"expectation_id": "e2", "requirement_id": "r2", "wms_kind": "lot",
                 "outcome": OUTCOME_NOT_APPLICABLE, "summary": "没探针", "details": {}},
                {"expectation_id": "e3", "requirement_id": "r3", "wms_kind": "sku",
                 "outcome": OUTCOME_INDETERMINATE, "summary": "判不了", "details": {}},
            ],
            "unknown_scenarios": [],
            "scenario_facts": [],
        })
        assert report["case_pass_rate"] == 100.0
        assert report["verification_determined"] is True
        assert report["not_applicable_count"] == 1
        assert report["indeterminate_count"] == 1

    def test_an_uncovered_requirement_is_reported(self):
        """需求集里有一条需求**一条预期都没有** → 覆盖判据红

        隔离：这条只查打包腿的分母。
        """
        report = packager_handler({
            "test_task_id": "tt_1",
            "requirements": [
                {"requirement_id": "r1"}, {"requirement_id": "r_missing"},
            ],
            "observations": [
                {"expectation_id": "e1", "requirement_id": "r1", "wms_kind": "fefo",
                 "outcome": OUTCOME_PASS, "summary": "ok", "details": {}},
            ],
            "unknown_scenarios": [],
            "scenario_facts": [],
        })
        assert report["requirement_case_coverage"] is False, (
            "有一条需求一条预期都没有，覆盖率却判绿"
        )
        assert report["uncovered_requirements"] == ["r_missing"], (
            f"没被覆盖的需求没被点名：{report['uncovered_requirements']}"
        )

    def test_an_unknown_scenario_is_reported(self):
        """投了一个不存在的场景 → `scenario_coverage` 红 + 点名"""
        report = packager_handler({
            "test_task_id": "tt_1",
            "requirements": _requirements(),
            "observations": [],
            "unknown_scenarios": ["不存在的场景"],
            "scenario_facts": [],
        })
        assert report["scenario_coverage"] is False, (
            "投了一个不存在的场景，覆盖率却判绿"
        )
        assert report["unknown_scenarios"] == ["不存在的场景"], (
            f"不存在的场景没被点名：{report['unknown_scenarios']}"
        )


# ============================================================
# 探针本身：红灯要带定位，假绿要挡住
# ============================================================


class TestTheProbesTellTheTruth:
    def test_the_fefo_probe_fails_when_the_expiry_is_lost(self):
        """效期丢了 → **判红**，哪怕这次恰好挑中了近效期批次

        「碰巧对」不是「满足需求」：效期不在系统里，
        分配排序键全是空，挑中哪个批次是巧合。

        **这条守卫挡的是一个真实的假绿**：真跑一次时
        fefo 判了 pass（近效期批次先入库），但那是巧合。

        隔离：这条直接测探针函数，不跑链路。
        """
        result = _check_fefo(_ctx(), _facts(expiry_lost=True, allocated_batch="B-NEAR"))
        assert result.outcome == OUTCOME_FAIL, (
            "效期都丢了还判通过——那是假绿"
        )
        assert "效期" in result.summary, f"红灯没说清原因：{result.summary}"

    def test_the_fefo_probe_passes_when_the_expiry_survived(self):
        """效期还在 + 挑中近效期批次 → 判绿（**别把判据写成永远红**）"""
        result = _check_fefo(_ctx(), _facts(expiry_lost=False, allocated_batch="B-NEAR"))
        assert result.outcome == OUTCOME_PASS

    def test_the_fefo_probe_fails_when_the_wrong_batch_is_picked(self):
        """效期还在，但挑中了远效期批次 → 判红"""
        result = _check_fefo(_ctx(), _facts(expiry_lost=False, allocated_batch="B-FAR"))
        assert result.outcome == OUTCOME_FAIL, (
            f"挑中了远效期批次却判 {result.outcome!r}"
        )

    def test_the_failure_carries_the_location_evidence(self):
        """红灯要**指到具体那一批**

        只给一个 False 的话，读者知道红了，但不知道该查哪张单据。
        """
        result = _check_fefo(_ctx(), _facts(expiry_lost=True, allocated_batch="B-FAR"))
        details = result.details
        assert details.get("allocated") == "B-FAR"
        assert details.get("expected_batch_no") == "B-NEAR"
        assert details.get("candidates"), "红灯没带候选批次"
        assert details.get("stored_inventory"), "红灯没带库存现状"

    def test_the_lot_probe_detects_the_lost_expiry(self):
        """「批次效期随货移动」这条独立判红

        隔离：这条直接测探针函数。
        """
        result = _check_lot_expiry(_ctx(), _facts(expiry_lost=True, allocated_batch="B-NEAR"))
        assert result.outcome == OUTCOME_FAIL, (
            f"效期丢了却判 {result.outcome!r}"
        )
        assert "效期" in result.summary, f"红灯没说清原因：{result.summary}"

    def test_the_lot_probe_passes_when_the_expiry_survived(self):
        result = _check_lot_expiry(_ctx(), _facts(expiry_lost=False, allocated_batch="B-NEAR"))
        assert result.outcome == OUTCOME_PASS

    def test_the_expiring_count_probe_compares_against_the_business_fact(self):
        """看板报 0，而库里**确实**有近效期批次 → 判红

        **判据用业务事实，不用系统自己的数**：
        场景明确放了近效期批次在库——那是真的。

        隔离：这条直接测探针函数。
        """
        facts = _facts(expiry_lost=False, allocated_batch="B-NEAR")
        result = _check_expiring_sku_count(_ctx(), facts)
        assert result.outcome == OUTCOME_FAIL, (
            f"看板报 0 而库里确实有近效期批次，却判 {result.outcome!r}"
        )
        assert "临期 SKU 数" in result.summary, f"红灯没说清原因：{result.summary}"

    def test_the_expiring_count_probe_passes_when_the_count_is_right(self):
        facts = _facts(expiry_lost=False, allocated_batch="B-NEAR")
        facts["dashboard"]["expiringSoonSkuCount"] = 1
        result = _check_expiring_sku_count(_ctx(), facts)
        assert result.outcome == OUTCOME_PASS

    def test_every_probe_is_registered_under_a_known_concept(self):
        """探针索引到的概念得是**词表里真有的概念**

        索引了一个不存在的概念 = 探针永远不会被叫到，
        而对应的需求会一直显示「没有探针」。

        隔离：这条只查注册表。
        """
        from boxes.modeling.domain import (
            WMS_CONSTRAINTS,
            WMS_ENTITIES,
            WMS_METRICS,
            WMS_REPORTS,
        )

        known = (
            set(WMS_ENTITIES) | set(WMS_CONSTRAINTS)
            | {m["name"] for m in WMS_METRICS} | set(WMS_REPORTS)
        )
        for concept in ("fefo", "lot", "wms_expiring_soon_sku_count",
                        "inventory_dashboard"):
            assert concept in known, f"探针索引的概念 {concept} 不在词表里"
            found = find_probe(concept)
            assert found is not None, f"概念 {concept} 找不到探针"

    def test_the_client_turns_a_business_error_into_an_exception(self, monkeypatch):
        """WMS 返回 `code != 0` → **抛异常**，不当成成功

        WMS 的业务错误是 HTTP 200 + `{"code": 50000, ...}`。
        **只看 HTTP 状态码的话，每个服务端错误都会被当成成功**——
        探针会拿着一份空数据去判「通过」。

        隔离：这条直接测客户端，不碰别的。
        """
        import json as _json
        import urllib.request as _urllib_request

        from boxes.wms_testing.wms_client import WmsClient

        class _Resp:
            def read(self):
                return _json.dumps(
                    {"code": 50000, "message": "服务端内部错误", "data": None}
                ).encode()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(
            _urllib_request, "urlopen", lambda *a, **kw: _Resp())
        with pytest.raises(WmsError) as exc:
            WmsClient("http://x/api/v1").dashboard()
        assert "50000" in str(exc.value), f"业务错误没说清：{exc.value}"

    def test_a_concept_without_a_probe_is_reported_as_such(self):
        """词表里有、但没写探针的概念 → find_probe 返回 None

        「没写」是事实，不是错误——但必须**明说**。

        隔离：这条只查注册表。
        """
        assert find_probe("warehouse") is None
