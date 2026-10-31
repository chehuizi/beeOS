"""tests.test_wms_ops_box - WMS 运维盒的守卫

运维盒是这只盒子里**最容易变成空壳**的一个：它的判据全是布尔，
而布尔最容易被写成「看起来对」的常量。

所以这组测试盯住三件事：

1. **它接上了上游**（输入里必须有 `ref:`）——没接上的运维盒
   没法验任何东西，只能是骨架
2. **判据的答案来自外部**：上游验收报告 / git / 告警条数 / 生产实例
3. **「用不上」不许变成「通过」**：没有变更就没有审批率——
   那条判据的取值是 "n/a"，不是 True
"""

from __future__ import annotations

import pytest

from boxes.wms_ops import get_definition
from boxes.wms_ops import rollback as rollback_check
from boxes.wms_ops import runtime_map
from boxes.wms_ops.runner import (
    ops_handle_handler,
    ops_intake_handler,
    ops_packager_handler,
)


def _artifact(commit_sha: str = "abcdef1234567890") -> dict:
    return {
        "commit_sha": commit_sha,
        "branch": f"beeos/dev-{commit_sha[:8]}",
        "artifact_digest": "sha256:deadbeefcafebabe",
        "commit_count": 1,
        "build_failed": False,
        "build_reproducibility": True,
        "unit_test_green": True,
        "review_coverage": True,
        "artifact_traceable": True,
    }


def _verification(**overrides) -> dict:
    report = {
        "test_task_id": "tt_1",
        "case_pass_rate": 100.0,
        "open_defect_count": 0,
        "no_blocking_defect": True,
        "verification_determined": True,
        "artifact_identified": True,
        "observations": [
            {
                "expectation_id": "exp_1",
                "requirement_id": "req_1",
                "wms_kind": "wms_expiring_soon_sku_count",
                "statement": "30 天内到期的 SKU 数",
                "evidence": "临期 30 天内 SKU",
                "outcome": "pass",
                "summary": "ok",
                "details": {},
            },
        ],
    }
    report.update(overrides)
    return report


# ============================================================
# 接线：它得接上上游，否则无从验起
# ============================================================


class TestWiring:
    def test_the_task_input_has_ref_inputs(self):
        """输入里必须有 `ref:` 字段

        以前运维盒的四个输入全是自带原始值，一个 `ref:` 都没有——
        它跟这条链没有任何关系，所以它只能是骨架盒。
        """
        schema = get_definition().get_schema("schema_ops_task_input")
        refs = [f.name for f in schema.fields if f.type.startswith("ref:")]
        assert "verification_ref" in refs, "运维盒没接测试盒的验收报告"
        assert "artifact_ref" in refs, "运维盒没接开发盒的制品"

    def test_the_declared_shapes_match_what_upstream_actually_produces(self):
        """抄两份形状会漂——直接比两份声明的字段名集合

        上游（测试盒 / 开发盒）的产出必须满足运维盒自己声明的形状，
        否则交接当场失败（响的），而不是「接过去了但读不到」（哑的）。
        """
        from boxes.wms_development.schemas import BUILD_ARTIFACT_SCHEMA
        from boxes.wms_testing.schemas import TEST_REPORT_SCHEMA

        ops = get_definition()
        ver_fields = {
            f.name for f in ops.get_schema("schema_ops_verification_input").fields
        }
        test_report_fields = {f.name for f in TEST_REPORT_SCHEMA.fields}
        assert ver_fields <= test_report_fields, (
            f"运维盒声明的验收形状比测试盒的产出还多："
            f"{sorted(ver_fields - test_report_fields)}"
        )

        art_fields = {
            f.name for f in ops.get_schema("schema_ops_artifact_input").fields
        }
        dev_fields = {f.name for f in BUILD_ARTIFACT_SCHEMA.fields}
        assert art_fields <= dev_fields, (
            f"运维盒声明的制品形状比开发盒的产出还多："
            f"{sorted(art_fields - dev_fields)}"
        )

    def test_the_box_no_longer_claims_to_be_a_skeleton(self):
        assert get_definition().result.maturity == "live"


# ============================================================
# 变更审批：验过 + 指名了那份构建
# ============================================================


class TestChangeApproval:
    def _run(self, change_request: str, verification: dict, artifact: dict) -> dict:
        intake = ops_intake_handler({
            "ops_task_id": "ops_1",
            "change_request": change_request,
            "alerts": [],
            "rollback_target": "abc1234",
            "verification_ref": verification,
            "artifact_ref": artifact,
        })
        return ops_handle_handler(intake)

    def test_a_change_without_verification_is_not_approved(self):
        out = self._run("放行 abcdef12 这份构建", {}, _artifact())
        assert out["changes"][0]["approved"] is False
        assert "验收报告" in out["changes"][0]["reason"]

    def test_a_change_that_does_not_name_the_build_is_not_approved(self):
        """验过 ≠ 可以放行：放的东西得是验过的那份

        变更单写「上线拣货准确率」，放行的却是另一份构建——
        那这份验收就白做了。
        """
        out = self._run("上线拣货准确率", _verification(), _artifact())
        assert out["changes"][0]["approved"] is False
        assert "指名" in out["changes"][0]["reason"]

    def test_a_verified_change_naming_the_build_is_approved(self):
        out = self._run("放行 abcdef12 这份构建", _verification(), _artifact())
        assert out["changes"][0]["approved"] is True

    def test_a_failed_verification_blocks_the_change(self):
        out = self._run(
            "放行 abcdef12 这份构建",
            _verification(no_blocking_defect=False, open_defect_count=2),
            _artifact(),
        )
        assert out["changes"][0]["approved"] is False

    def test_a_low_pass_rate_blocks_the_change(self):
        out = self._run(
            "放行 abcdef12 这份构建",
            _verification(case_pass_rate=33.33),
            _artifact(),
        )
        assert out["changes"][0]["approved"] is False
        assert "99" in out["changes"][0]["reason"]


# ============================================================
# 回滚能力：目标取得到吗
# ============================================================


class TestRollbackReadiness:
    def test_an_unresolvable_target_is_not_ready(self, monkeypatch):
        monkeypatch.setenv(rollback_check.ARTIFACT_STORE_ENV, "/tmp/beeos-no-such-store")
        monkeypatch.delenv("BEEOS_WMS_REPO", raising=False)
        ok, reason, _detail = rollback_check.resolve("sha256:00000000")
        assert ok is False
        assert reason

    def test_an_empty_target_is_not_ready(self):
        ok, reason, _detail = rollback_check.resolve("")
        assert ok is False
        assert "回滚目标" in reason

    def test_a_target_in_the_artifact_store_is_ready(self, tmp_path, monkeypatch):
        store = tmp_path / "artifacts"
        (store / "abcdef12").mkdir(parents=True)
        (store / "abcdef12" / "meta.json").write_text(
            '{"commit_sha": "abcdef1234567890", "artifact_digest": "sha256:deadbeefcafebabe"}',
            encoding="utf-8",
        )
        monkeypatch.setenv(rollback_check.ARTIFACT_STORE_ENV, str(store))
        ok, reason, _detail = rollback_check.resolve("sha256:deadbeef")
        assert ok is True, reason

    def test_a_missing_toolchain_says_so_instead_of_claiming_the_target_is_gone(
        self, monkeypatch,
    ):
        """「环境不具备」和「目标不存在」是两件事

        没配 BEEOS_WMS_REPO 的时候，理由不能写成「仓里没有这个提交」——
        那会把人引去改一个并不存在的问题。
        """
        monkeypatch.delenv("BEEOS_WMS_REPO", raising=False)
        ok, reason, _detail = rollback_check.resolve("abcdef1234567890")
        assert ok is False
        assert "BEEOS_WMS_REPO" in reason


# ============================================================
# 故障处置：每条告警都有记录
# ============================================================


class TestIncidentDocumentation:
    def test_every_alert_gets_a_record(self):
        intake = ops_intake_handler({
            "ops_task_id": "ops_1",
            "change_request": "",
            "alerts": ["拣货超时", "库存效期告警"],
            "rollback_target": "",
            "verification_ref": {},
            "artifact_ref": {},
        })
        out = ops_handle_handler(intake)
        assert len(out["incidents"]) == 2
        assert {i["alert"] for i in out["incidents"]} == {"拣货超时", "库存效期告警"}
        assert all(i["status"] == "documented" for i in out["incidents"])

    def test_an_unrecognisable_alert_is_not_guessed_into_a_category(self):
        """认不出就标 unclassified，不猜

        把「拣货超时」说成「库存问题」比说「不知道」更糟——
        它会让处置走错流程。
        """
        assert runtime_map.classify_alert("拣货超时") == "outbound"
        assert runtime_map.classify_alert("库存效期告警") == "inventory"
        assert runtime_map.classify_alert("天上掉下来个东西") == "unclassified"


# ============================================================
# 线上真实状态：读到了才算
# ============================================================


class TestRuntimeState:
    def _handle(self, monkeypatch, dashboard: dict, health: dict, error: str = ""):
        from boxes.wms_ops import runner as ops_runner

        class FakeReader:
            def __init__(self, base_url, timeout=15.0):
                self.base_url = base_url

            def dashboard(self):
                if error:
                    raise ops_runner.ReaderError(error)
                return dashboard

            def health(self):
                if error:
                    raise ops_runner.ReaderError(error)
                return health

        monkeypatch.setattr(ops_runner, "WmsReader", FakeReader)
        intake = ops_intake_handler({
            "ops_task_id": "ops_1",
            "change_request": "",
            "alerts": [],
            "rollback_target": "",
            "target_base_url": "http://prod.example/api/v1",
            "verification_ref": {},
            "artifact_ref": {},
        })
        return ops_handle_handler(intake)

    def test_an_unreachable_production_is_not_recorded(self, monkeypatch):
        out = self._handle(monkeypatch, {}, {}, error="连不上生产")
        assert out["runtime_state"]["reachable"] is False
        assert "连不上" in out["runtime_state"]["error"]

    def test_a_reachable_production_is_recorded_with_its_identity(self, monkeypatch):
        out = self._handle(
            monkeypatch,
            {"expiringSoonSkuCount": 3, "totalOnHandQty": 10},
            {"status": "UP"},
        )
        state = out["runtime_state"]
        assert state["reachable"] is True
        assert state["base_url"] == "http://prod.example/api/v1"
        assert state["observed_at"]
        assert "expiringSoonSkuCount" in state["business_fields"]

    def test_a_missing_production_address_is_said_out_loud(self, monkeypatch):
        monkeypatch.delenv("BEEOS_WMS_BASE_URL", raising=False)
        intake = ops_intake_handler({
            "ops_task_id": "ops_1",
            "change_request": "",
            "alerts": [],
            "rollback_target": "",
            "verification_ref": {},
            "artifact_ref": {},
        })
        out = ops_handle_handler(intake)
        assert out["runtime_state"]["reachable"] is False
        assert "BEEOS_WMS_BASE_URL" in out["runtime_state"]["error"]


# ============================================================
# 生产上复算：只对读得到的概念做
# ============================================================


class TestProductionObservations:
    def _handle(self, monkeypatch, dashboard: dict, error: str = ""):
        from boxes.wms_ops import runner as ops_runner

        class FakeReader:
            def __init__(self, base_url, timeout=15.0):
                self.base_url = base_url

            def dashboard(self):
                if error:
                    raise ops_runner.ReaderError(error)
                return dashboard

            def health(self):
                return {"status": "UP"}

        monkeypatch.setattr(ops_runner, "WmsReader", FakeReader)
        intake = ops_intake_handler({
            "ops_task_id": "ops_1",
            "change_request": "",
            "alerts": [],
            "rollback_target": "",
            "target_base_url": "http://prod.example/api/v1",
            "verification_ref": _verification(),
            "artifact_ref": {},
        })
        return ops_handle_handler(intake)

    def test_a_metric_missing_from_production_is_a_failure_not_a_pass(self, monkeypatch):
        """生产看板上没有这个数 → fail（不是「验不了」）

        这正是新加的指标还没部署上去时该说的话。
        """
        out = self._handle(monkeypatch, {"totalOnHandQty": 10})
        obs = out["production_observations"]
        assert len(obs) == 1
        assert obs[0]["outcome"] == "fail"
        assert "expiringSoonSkuCount" in obs[0]["summary"]

    def test_a_metric_present_in_production_passes(self, monkeypatch):
        out = self._handle(monkeypatch, {"expiringSoonSkuCount": 3})
        obs = out["production_observations"][0]
        assert obs["outcome"] == "pass"
        assert obs["details"]["value"] == 3
        assert obs["details"]["measured_base_url"] == "http://prod.example/api/v1"

    def test_an_unreachable_production_is_indeterminate_not_fail(self, monkeypatch):
        """读不到 = 现场没条件，不是「需求没被满足」"""
        out = self._handle(monkeypatch, {}, error="连不上生产")
        assert out["production_observations"][0]["outcome"] == "indeterminate"

    def test_concepts_without_a_production_read_method_are_not_observed(self, monkeypatch):
        """没有生产读法的概念不产结论（保留验收实例上的那条）"""
        from boxes.wms_ops import runner as ops_runner

        class FakeReader:
            def __init__(self, base_url, timeout=15.0):
                self.base_url = base_url

            def dashboard(self):
                return {"expiringSoonSkuCount": 3}

            def health(self):
                return {"status": "UP"}

        monkeypatch.setattr(ops_runner, "WmsReader", FakeReader)
        intake = ops_intake_handler({
            "ops_task_id": "ops_1",
            "change_request": "",
            "alerts": [],
            "rollback_target": "",
            "target_base_url": "http://prod.example/api/v1",
            "verification_ref": _verification(observations=[
                {"expectation_id": "exp_x", "requirement_id": "req_x",
                 "wms_kind": "fefo", "outcome": "pass", "summary": "ok",
                 "details": {}},
            ]),
            "artifact_ref": {},
        })
        out = ops_handle_handler(intake)
        assert out["production_observations"] == []


# ============================================================
# 「用不上」不许变成「通过」
# ============================================================


class TestNotApplicableIsNotPass:
    def _record(self, **kwargs) -> dict:
        payload = {
            "ops_task_id": "ops_1",
            "change_count": 0,
            "incident_count": 0,
            "changes": [],
            "incidents": [],
            "rollback_target": "",
            "rollback_ready": False,
            "rollback_reason": "没给回滚目标",
            "runtime_state": {
                "observed_at": "2026-10-26T00:00:00+00:00",
                "base_url": "http://prod.example/api/v1",
                "mode": "production",
                "reachable": True,
                "dashboard": {"expiringSoonSkuCount": 1},
                "health": {"status": "UP"},
                "business_fields": ["expiringSoonSkuCount"],
                "error": "",
            },
            "production_observations": [],
            "artifact_ref": {},
        }
        payload.update(kwargs)
        return ops_packager_handler(payload)

    def test_no_change_means_not_applicable_not_true(self):
        record = self._record()
        assert record["change_approval_rate"] == "n/a"
        assert record["rollback_readiness"] == "n/a"

    def test_no_alert_means_not_applicable_not_true(self):
        record = self._record()
        assert record["incident_documented"] == "n/a"

    def test_an_empty_run_is_not_determined(self):
        """什么都没干 → 不许「全绿」

        三条 n/a + 一条通过，看起来像「基本没问题」——
        那是假信号。
        """
        record = self._record()
        assert record["ops_determined"] is False

    def test_a_run_with_real_work_and_a_reading_is_determined(self):
        record = self._record(
            change_count=1,
            changes=[{"change_id": "chg_1", "text": "x", "approved": True}],
            incident_count=1,
            incidents=[{"incident_id": "inc_1", "alert": "拣货超时",
                        "category": "outbound", "status": "documented"}],
        )
        assert record["ops_determined"] is True

    def test_an_unreachable_production_is_not_determined(self):
        record = self._record(
            change_count=1,
            changes=[{"change_id": "chg_1", "text": "x", "approved": True}],
            runtime_state={"reachable": False, "observed_at": "", "error": "连不上"},
        )
        assert record["runtime_state_recorded"] is False
        assert record["ops_determined"] is False


# ============================================================
# 零改动的交付照样要放行（2026-10-30 实测撞出来的）
#
# 开发盒诚实地说「这次没有新改动（方案已落地）」→ 没有 commit_sha →
# 链脚本给的变更单成了空字符串 → 运维盒收到一条空变更单 →
# 「什么都没干」→ 判红。
#
# 可这次**照样要放行一份构建**——那份构建来自基线提交。
# 变更单问的是「要放哪份构建」，不是「有没有新提交」。
# ============================================================


class TestAZeroChangeDeliveryIsStillReleased:
    def _artifact_without_a_new_commit(self) -> dict:
        """零改动：没有新提交，制品来自基线提交"""
        return {
            "commit_sha": "",
            "branch": "",
            "base_commit": "449a6992275e0e09910ee92563a04e542ca14b39",
            "artifact_digest": "sha256:deadbeefcafebabe",
            "commit_count": 0,
            "build_failed": False,
            "build_reproducibility": True,
            "unit_test_green": True,
            "review_coverage": True,
            "artifact_traceable": True,
        }

    def _run(self, change_request: str, artifact: dict) -> dict:
        intake = ops_intake_handler({
            "ops_task_id": "ops_1",
            "change_request": change_request,
            "alerts": [],
            "rollback_target": "abc1234",
            "verification_ref": _verification(),
            "artifact_ref": artifact,
        })
        return ops_handle_handler(intake)

    def test_a_change_naming_the_baseline_build_is_approved(self):
        """指名基线那份构建 = 指名了要放的东西"""
        out = self._run(
            "放行 449a6992 这份构建（zone；本次没有新改动，方案已落地）",
            self._artifact_without_a_new_commit(),
        )
        assert out["changes"][0]["approved"] is True, out["changes"][0]["reason"]
        assert "449a6992" in out["changes"][0]["reason"]

    def test_a_change_naming_another_build_is_not_approved(self):
        """指名别的构建照样不行——放的东西得是验过的那份"""
        out = self._run(
            "放行 00000000 这份构建",
            self._artifact_without_a_new_commit(),
        )
        assert out["changes"][0]["approved"] is False
        assert "指名" in out["changes"][0]["reason"]

    def test_the_chain_builds_a_change_request_even_without_a_new_commit(self):
        """链脚本：没有新提交也要给出一份**指名了构建**的变更单

        空变更单 → 运维盒「什么都没干」→ 判红。
        """
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "scripts" / "run_chain.py"
        spec = importlib.util.spec_from_file_location("run_chain_under_test", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        request = mod._build_change_request(
            {"commit_sha": "", "base_commit": "449a6992275e0e09910ee92563a04e542ca14b39"},
            [{"wms_kind": "zone"}, {"wms_kind": "location"}],
        )
        assert "449a6992" in request
        assert "zone" in request
        assert "没有新改动" in request, (
            f"变更单没说清这次没改代码：{request}"
        )

    def test_the_change_request_names_the_new_commit_when_there_is_one(self):
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "scripts" / "run_chain.py"
        spec = importlib.util.spec_from_file_location("run_chain_under_test2", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        request = mod._build_change_request(
            {"commit_sha": "abcdef1234567890", "base_commit": "449a6992275e0e09910ee92563a04e542ca14b39"},
            [{"wms_kind": "zone"}],
        )
        assert "abcdef12" in request
        assert "没有新改动" not in request
