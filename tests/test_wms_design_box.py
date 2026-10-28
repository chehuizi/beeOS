"""tests.test_wms_design_box - 业务建模盒 Definition + Beeline + 端到端

覆盖范围：
- boxes.wms_design definition 加载 + 5 机械 acceptance + 3 exception
- boxes.wms_design.beeline 加载 + 7 ops 顺序链
- 端到端：requirement set → parse → classify → generate → verify → evidence → ACCEPTED
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from boxes.wms_design import WMS_DESIGN_BOX, get_definition
from boxes.wms_design.runner import RUNNER as MODELING_RUNNER
from boxes.wms_design.measure import COMPUTERS as MODEL_COMPUTERS
from boxes.wms_design.schemas import REQUIREMENT_TYPES
from boxes.wms_design.beeline import WMS_DESIGN_BEELINE, get_beeline
from core import metrics as m
from runtime import (
    BeelineExecutor,
    AcceptanceStatus,
    TaskRunStatus,
    create_task_run,
    evaluate_acceptance,
)
from runtime.contract import validate_payload

# 建模盒的 WMS 绑定走 LLM → 这组测试要桩（分流桩：抽取腿 / 绑定腿都认）
pytestmark = pytest.mark.usefixtures("stub_llm")


# ============================================================
# 测试 fixture
# ============================================================


def _sample_requirement_set() -> dict:
    """示例 WMS 需求集

    原来是**退款域**的需求（「订单实体 / 退款必须在 7 天内」）。
    盒子变成 WMS 建模盒之后，那套语料就该被 wms_binding 打回——
    「退款必须在 7 天内」跟仓储一点关系都没有，拿它当「应该通过」的样本
    等于教这个盒子在不属于自己的域里说通过。

    换成真 WMS 需求：每一条都能落到一个 WMS 概念上。
    """
    return {
        "set_id": "wms_req_set_001",
        "business_goal": "把入库上架和拣货出库管清楚",
        "requirements": [
            {"requirement_id": "req_obj_1", "description": "库存记录要按 SKU 和库位分别记账",
             "requirement_type": "object", "priority": "must_have"},
            {"requirement_id": "req_obj_2", "description": "批次要记录生产日期和失效日期",
             "requirement_type": "object", "priority": "must_have"},
            {"requirement_id": "req_rule_3", "description": "同一 SKU 在同一库位不可重复占用",
             "requirement_type": "rule", "priority": "must_have"},
            {"requirement_id": "req_proc_5", "description": "收货后按库位策略上架",
             "requirement_type": "process", "priority": "must_have"},
            {"requirement_id": "req_metric_7", "description": "拣货准确率要达到 99.5%",
             "requirement_type": "metric", "priority": "should_have"},
        ],
    }


def _off_domain_requirement_set() -> dict:
    """**故意非 WMS** 的需求集——用来证明 wms_binding 会打回投料错误

    「用户登录要用密码」这种需求混进 WMS 建模盒，说明**投料错了**，
    不是「建模质量差一点」。该打回，而且要能点名是哪几条。
    """
    base = _sample_requirement_set()
    base = json.loads(json.dumps(base))
    base["requirements"].append(
        {"requirement_id": "req_off_1", "description": "用户登录要用密码",
         "requirement_type": "object", "priority": "must_have"})
    return base


# ============================================================
# Definition 验证
# ============================================================


class TestModelingBoxDefinition:
    def test_module_exports(self):
        assert callable(get_definition)
        assert isinstance(WMS_DESIGN_BOX, __import__("core.models").models.BeeBoxDefinition)

    def test_id_and_version(self):
        d = get_definition()
        assert d.id == "wms_design_box"
        # version 2 = 从 business_modeling_box 迁到 wms_design_box 那一刀。
        # 契约变了（多两条 WMS 判据、模型元素多两个 WMS 字段），版本得跟着涨——
        # 版本号不涨的话，调用方没法区分「同一个盒子的新版本」和「另一个盒子」。
        assert d.version == 2

    def test_schemas_complete(self):
        d = get_definition()
        ids = {s.id for s in d.schemas}
        # 7 个 schema 必须就位
        for sid in (
            "schema_business_requirement",
            "schema_business_requirement_set",
            "schema_parsed_requirements",
            "schema_classified_requirements",
            "schema_model_elements",
            "schema_model_evidence",
            "schema_business_model_package",
        ):
            assert sid in ids, f"missing schema {sid}"

    def test_task_references_real_schema(self):
        d = get_definition()
        t = d.get_task("handle_modeling_request")
        assert t is not None
        # task_schema 必须真实存在
        assert d.get_schema(t.task_schema) is not None
        # beeline 引用
        assert t.beeline_id == "beeline_wms_design_v1"
        assert t.beeline_version == 1

    def test_acceptance_all_machine_verifiable(self):
        """8 条 acceptance 必须是机器可验字段（无主观项）

        **第 6、7 条是 WMS 专属的**：前 5 条验「模型自洽」，它们验
        「**这是不是一个 WMS 模型**」——盒子挂着 WMS 的招牌，
        就不能只验自洽性。

        **第 8 条验「预期载体有没有被带出来」**：
        模型认出来的每个 WMS 概念，都要变成一条可判定的预期。
        少了它，下游（测试盒 / 分析盒）拿到空的预期集，
        「验证业务价值」就静悄悄地退化成「什么都没验」。
        """
        d = get_definition()
        metrics = [a.metric for a in d.result.acceptance]
        assert set(metrics) == {
            "requirement_coverage",
            "rule_consistency",
            "reference_integrity",
            "structural_compliance",
            "metrics_defined",
            "wms_binding",
            "wms_rule_binding",
            "expectation_completeness",
        }
        for a in d.result.acceptance:
            assert a.value in (True, 100, 100.0) or (isinstance(a.value, (int, float)) and a.value >= 100)

    def test_exceptions_cover_typical_failures(self):
        d = get_definition()
        actions = {e.action for e in d.result.exceptions}
        assert actions == {"escalate", "rollback", "no_deliver"}

    def test_queen_within_bounds(self):
        d = get_definition()
        a = d.queen.authorization
        for v in (a.task_routing, a.exception_handling, a.continuous_improvement):
            assert v in {"allow", "observe_only", "off"}

    def test_declared_metrics_all_have_a_collector(self):
        """声明的每个指标都必须有实测采集算法

        这条取代了旧的「三个维度都要有指标」——那个断言逼着盒子去声明
        cost 指标，可成本压根没有记账信号源，只能声明一个算不出来的数字。
        维度齐不齐无所谓，指标有没有出处才要紧。
        """
        d = get_definition()
        assert m.unwired(d, MODEL_COMPUTERS) == []
        assert m.orphan_computers(d, MODEL_COMPUTERS) == []


# ============================================================
# Beeline 验证
# ============================================================


class TestModelingBeeline:
    def test_module_exports(self):
        assert callable(get_beeline)
        assert WMS_DESIGN_BEELINE.id == "beeline_wms_design_v1"
        assert WMS_DESIGN_BEELINE.version == 1

    def test_seven_op_linear_chain(self):
        b = get_beeline()
        op_ids = [op.op_id for op in b.operations]
        assert op_ids == [
            "parse_requirements",
            "classify_requirements",
            "generate_model_elements",
            "verify_coverage",
            "check_consistency",
            "generate_evidence",
            "package_model",
        ]

    def test_entry_point(self):
        b = get_beeline()
        starts = b.get_start_operations()
        assert len(starts) == 1
        assert starts[0].op_id == "parse_requirements"

    def test_terminal_at_package_model(self):
        b = get_beeline()
        pkg = b.get_operation("package_model")
        assert pkg is not None
        assert pkg.next == []

    def test_all_ops_idempotent_required(self):
        b = get_beeline()
        for op in b.operations:
            assert op.idempotency is not None
            assert op.idempotency.mode == "required"
            assert op.idempotency.key == "set_id"

    def test_beeline_id_matches_definition_reference(self):
        """beeline id + version 必须与 definition.task.beeline_id + beeline_version 一致"""
        box = get_definition()
        beeline = get_beeline()
        t = box.get_task("handle_modeling_request")
        assert t.beeline_id == beeline.id
        assert t.beeline_version == beeline.version


# ============================================================
# 端到端：definition + beeline + executor + acceptance
# ============================================================


class TestModelingEndToEnd:
    def test_full_flow_accepts(self):
        """完整流程：requirement set → ACCEPTED"""
        box = get_definition()
        beeline = get_beeline()

        input_data = _sample_requirement_set()

        task_run = create_task_run(
            originator="pm.api",
            intent="handle_modeling_request",
            contract_ref=box.result.result_schema,
            release_id=f"{box.id}@v{box.version}",
            beeline_id=beeline.id,
            beeline_version=beeline.version,
            runtime_id="runtime_dev",
            instance_id="instance_local",
        )

        # 执行 beeline
        task_run = BeelineExecutor(MODELING_RUNNER).execute(task_run, beeline, input_data)

        # 验证：task run COMPLETED
        assert task_run.status == TaskRunStatus.COMPLETED
        # 验证：所有 op 都跑过
        assert set(task_run.operations.keys()) == {
            "parse_requirements",
            "classify_requirements",
            "generate_model_elements",
            "verify_coverage",
            "check_consistency",
            "generate_evidence",
            "package_model",
        }
        for rec in task_run.operations.values():
            assert rec.status == "completed"

        # 验证：result 含 5 项 acceptance 字段
        result = task_run.result
        assert result["requirement_coverage"] == 100.0
        assert result["rule_consistency"] is True
        assert result["reference_integrity"] is True
        assert result["structural_compliance"] is True
        assert result["metrics_defined"] is True

        # 验证：acceptance ACCEPTED
        eval_result = evaluate_acceptance(task_run, box.result)
        assert eval_result.status == AcceptanceStatus.ACCEPTED
        assert eval_result.passed_rules == [
            "requirement_coverage",
            "rule_consistency",
            "reference_integrity",
            "structural_compliance",
            "metrics_defined",
            "wms_binding",
            "wms_rule_binding",
            "expectation_completeness",
        ]
        assert eval_result.failed_rules == []

    def test_event_log_complete(self):
        box = get_definition()
        beeline = get_beeline()

        task_run = create_task_run(
            "pm.api", "handle_modeling_request", box.result.result_schema,
            f"{box.id}@v{box.version}", beeline.id, beeline.version,
            "rt", "in",
        )
        BeelineExecutor(MODELING_RUNNER).execute(task_run, beeline, _sample_requirement_set())

        events = [e.event for e in task_run.event_log.entries]
        # 必须包含 status_change + op_start + op_finish
        assert "status_change" in events
        assert "op_start" in events
        assert "op_finish" in events
        # 7 op × 2 = 14 op_* 事件
        assert events.count("op_start") == 7
        assert events.count("op_finish") == 7

    def test_different_lines_dont_cross_contaminate(self):
        """不同盒子的判据互不污染"""
        from boxes.wms_requirement import get_definition as get_req_def
        from boxes.wms_requirement.beeline import get_beeline as get_req_beeline

        req_def = get_req_def()
        req_beeline = get_req_beeline()
        mod_def = get_definition()
        mod_beeline = get_beeline()

        # id 必须不同
        assert req_def.id != mod_def.id
        assert req_beeline.id != mod_beeline.id
        # acceptance 字段必须不同
        req_metrics = {a.metric for a in req_def.result.acceptance}
        mod_metrics = {a.metric for a in mod_def.result.acceptance}
        assert "source_coverage" in req_metrics        # 需求盒特征
        assert "requirement_coverage" in mod_metrics   # 建模盒特征
        # 不能混入
        assert "source_coverage" not in mod_metrics
        assert "requirement_coverage" not in req_metrics

    def test_minimal_requirement_set_works(self):
        """最小可行输入：1 个 object 需求"""
        minimal = {
            "set_id": "req_min",
            "business_goal": "minimal test",
            "requirements": [
                {"requirement_id": "req_obj_1", "description": "x", "requirement_type": "object", "priority": "must_have"},
            ],
        }
        box = get_definition()
        beeline = get_beeline()
        task_run = create_task_run(
            "pm", "handle_modeling_request", box.result.result_schema,
            f"{box.id}@v{box.version}", beeline.id, beeline.version,
            "rt", "in",
        )
        task_run = BeelineExecutor(MODELING_RUNNER).execute(task_run, beeline, minimal)
        assert task_run.status == TaskRunStatus.COMPLETED
        assert task_run.result["requirement_coverage"] == 100.0


# ============================================================
# Mock 数据流语义（parse 透传载荷 / classify 按真实 type / 覆盖率真算）
# ============================================================


class TestModelingMockSemantics:
    def _run(self, requirements, set_id="req_sem"):
        box = get_definition()
        beeline = get_beeline()
        task_run = create_task_run(
            "pm", "handle_modeling_request", box.result.result_schema,
            f"{box.id}@v{box.version}", beeline.id, beeline.version,
            "rt", "in",
        )
        return BeelineExecutor(MODELING_RUNNER).execute(
            task_run, beeline,
            {"set_id": set_id, "business_goal": "g", "requirements": requirements},
        )

    def test_the_produced_package_satisfies_its_own_result_schema(self):
        """产出的模型包必须满足 result schema 声明的形状

        以前**不满足**（12 条契约错）：
        - `evidence` 是手挑的 8 个字段，把 `set_id` / `business_goal` /
          `requirement_count` 漏在外面，而 schema 声明它们必填
        - `model_elements` 装的是 DDD 五组，schema 声明的是 op 产出的形状

        而没人看见——`ref:` 以前不做深校验，产出从来不被自己的契约检查过。
        「挑几个字段」= 挑的时候就会漏；整份搬过来就不会。
        """
        box = get_definition()
        schema = box.get_schema(box.result.result_schema)
        task_run = self._run(_sample_requirement_set()["requirements"])
        errors = validate_payload(task_run.result, schema, schemas=box.schemas)
        assert errors == [], f"产出的模型包不满足自己的 result schema：{errors}"

    def test_classify_by_declared_type_not_id_suffix(self):
        """分类按声明的 requirement_type，id 尾号不影响分类"""
        task_run = self._run([
            # 尾号 4 旧逻辑会误判为 rule，但声明是 metric
            {"requirement_id": "req_4", "description": "x", "requirement_type": "metric", "priority": "must_have"},
            # 尾号 7 旧逻辑会误判为 metric，但声明是 object
            {"requirement_id": "req_7", "description": "x", "requirement_type": "object", "priority": "must_have"},
        ])
        by_type = task_run.operations["classify_requirements"].output["by_type"]
        assert by_type["metric"] == ["req_4"]
        assert by_type["object"] == ["req_7"]

    def test_five_types_all_present_accepted(self):
        """五类需求齐全（含 goal）：goal 不计入覆盖率，5 类全覆盖 → ACCEPTED"""
        # description 必须**每条都含 WMS 概念**。以前这里是 "x"——
        # 那时盒子是通用的，"x" 无所谓。现在是 WMS 盒，需求讲的是
        # 仓储本身，「x」既绑不上实体也绑不上规则，wms_binding 会红。
        WMS_TEXT = {
            "object": "库存要按 SKU 和库位记账",
            "rule": "同一 SKU 在同一库位不可重复占用",
            "process": "收货后按库位策略上架",
            "metric": "拣货准确率要达到 99.5%",
            "goal": "把仓储管清楚",
            "event": "上架完成事件",
        }
        reqs = [
            {"requirement_id": f"r_{t}", "description": WMS_TEXT[t],
             "requirement_type": t, "priority": "must_have"}
            for t in ("object", "rule", "process", "metric", "goal")
        ]
        box = get_definition()
        task_run = self._run(reqs)
        assert task_run.result["requirement_coverage"] == 100.0
        assert task_run.result["metrics_defined"] is True
        assert evaluate_acceptance(task_run, box.result).status == AcceptanceStatus.ACCEPTED

    def test_by_type_schema_declares_every_requirement_type(self):
        """by_type 的 schema 必须覆盖全部 6 类 type（含 event / goal）

        runtime/classify 按 REQUIREMENT_TYPES 建全部 key；schema 少列一类，
        声明就名存实亡——契约校验形同虚设，且没人会发现 event 组悄悄丢了。
        """
        schema = get_definition().get_schema("schema_classified_requirements")
        by_type = next(f for f in schema.fields if f.name == "by_type")
        declared = {p.name for p in by_type.properties}
        assert declared == set(REQUIREMENT_TYPES)

    def test_by_type_output_passes_contract_validation(self):
        """classify 的真实输出能过 schema_classified_requirements 校验"""
        schema = get_definition().get_schema("schema_classified_requirements")
        task_run = self._run([
            {"requirement_id": f"r_{t}", "description": "x", "requirement_type": t, "priority": "must_have"}
            for t in ("object", "rule", "process", "metric", "event", "goal")
        ])
        out = task_run.operations["classify_requirements"].output
        assert validate_payload(out, schema) == []

    def test_no_metric_requirement_rejected(self):
        """无 metric 类需求 → metrics_defined=False → REJECTED"""
        box = get_definition()
        task_run = self._run([
            {"requirement_id": "r_obj", "description": "x", "requirement_type": "object", "priority": "must_have"},
        ])
        assert task_run.result["requirement_coverage"] == 100.0
        assert task_run.result["metrics_defined"] is False
        assert evaluate_acceptance(task_run, box.result).status == AcceptanceStatus.REJECTED

    def test_invalid_type_excluded_from_coverage(self):
        """type 非法的需求被 parse 剔除，不进覆盖率分母"""
        task_run = self._run([
            {"requirement_id": "r_ok", "description": "x", "requirement_type": "object", "priority": "must_have"},
            {"requirement_id": "r_bad", "description": "x", "requirement_type": "bogus", "priority": "must_have"},
        ])
        parse_out = task_run.operations["parse_requirements"].output
        assert parse_out["valid_ids"] == ["r_ok"]
        assert parse_out["parse_errors"] == ["invalid requirement_type for r_bad"]
        assert task_run.result["requirement_coverage"] == 100.0
        assert task_run.result["requirement_count"] == 1

    def test_coverage_verifier_detects_uncovered(self):
        """coverage_verifier 真实对比：未被 trace 覆盖的需求进入 uncovered"""
        from boxes.wms_design.runner import RUNNER
        out = RUNNER.run("coverage_verifier", {
            "set_id": "s",
            "modelable_ids": ["r1", "r2", "r3"],
            "entities": [{"entity_id": "e1", "name": "E", "trace_to": "r1"}],
            "rules": [{"rule_id": "ru2", "condition": "c", "action": "a", "trace_to": "r2"}],
            "processes": [],
            "metrics": [],
        })
        assert out["__coverage__"] == round(100.0 * 2 / 3, 2)
        assert out["__uncovered__"] == ["r3"]

    def test_consistency_checker_detects_broken_reference(self):
        """consistency_checker 真实检查：trace_to 指向无效需求 → reference_integrity=False"""
        from boxes.wms_design.runner import RUNNER
        out = RUNNER.run("consistency_checker", {
            "set_id": "s",
            "valid_ids": ["r1"],
            "entities": [{"entity_id": "e1", "name": "E", "trace_to": "r_ghost"}],
            "rules": [],
            "processes": [],
            "metrics": [],
        })
        assert out["__reference_integrity__"] is False
        assert out["__broken_references__"] == ["e1"]

    def test_business_goal_flows_to_package(self):
        """business_goal 从输入一路透传到最终 package"""
        task_run = self._run([
            {"requirement_id": "r_obj", "description": "x", "requirement_type": "object", "priority": "must_have"},
        ])
        assert task_run.result["business_goal"] == "g"
    def test_event_type_produces_domain_event(self):
        """event 类需求 → 领域事件元素（DDD 战术设计），计入覆盖率；不指派聚合根"""
        # 需求文案也 WMS 化了：以前是「订单 / 退款已完成 / 时长」——
        # 那是退款域的语料，盒子变 WMS 之后它就该被 wms_binding 打回。
        task_run = self._run([
            {"requirement_id": "r_obj", "description": "库存按 SKU 记账", "requirement_type": "object", "priority": "must_have"},
            {"requirement_id": "r_evt", "description": "上架已完成", "requirement_type": "event", "priority": "must_have"},
            {"requirement_id": "r_met", "description": "拣货准确率", "requirement_type": "metric", "priority": "must_have"},
        ])
        events = task_run.operations["generate_model_elements"].output["events"]
        # wms_kind 一并断言：它跟 pattern 是两回事——pattern 说「这是领域
        # 事件」，wms_kind 说「这是仓储哪个概念的变动」。少了后者，
        # 事件可以随便叫，盒子就退回通用建模了。
        assert events == [
            {"event_id": "evt_r_evt", "name": "上架已完成",
             "pattern": "domain_event", "wms_kind": "inbound",
             # 绑定是**列表**（带证据）：wms_kind 只是主项。
             # 一个元素可能同时绑好几个 WMS 概念。
             "wms_bindings": [{"kind": "inbound", "evidence": "上架"}],
             "trace_to": "r_evt"}
        ]
        assert task_run.result["requirement_coverage"] == 100.0
        model = task_run.result["model_elements"]
        assert model["domain_events"][0]["pattern"] == "domain_event"
        # 聚合根是一致性边界，没有不变量分析就不指派
        assert "aggregate_roots" not in model
        # object 一律建模为实体
        assert all(e["pattern"] == "entity" for e in model["entities"])


class TestWmsDomainIsRealNotJustTheName:
    """盒子写着 WMS，schema 和判据里就得真有 WMS

    只改 `display_name` 而不动 schema / 判据，等于**换个招牌的通用盒**——
    跟这个项目反复治的那个病一个样（判据说覆盖了，实际验的不是那件事）。
    """

    def _run(self, requirements, set_id="wms_dom"):
        """跑一遍完整 beeline，拿产出包——判据要在真实链路上验过才算数"""
        box = get_definition()
        beeline = get_beeline()
        task_run = create_task_run(
            "pm", "handle_modeling_request", box.result.result_schema,
            f"{box.id}@v{box.version}", beeline.id, beeline.version, "rt", "in",
        )
        return BeelineExecutor(MODELING_RUNNER).execute(
            task_run, beeline,
            {"set_id": set_id, "business_goal": "g", "requirements": requirements},
        )

    def test_the_vocabulary_lives_in_the_box_not_in_core(self):
        """WMS 是业务词汇，词表必须跟着盒子走

        放 core 的话，内核就知道了「仓储有 SKU 和波次」——那是业务知识。
        """
        import boxes.wms_design.domain as domain

        assert len(domain.WMS_ENTITIES) >= 8, "WMS 实体词表太空，说不出口袋"
        assert "sku" in domain.WMS_ENTITIES
        assert "location" in domain.WMS_ENTITIES
        assert "wave" in domain.WMS_ENTITIES

        # core 不许 import 盒子
        core_src = (Path(__file__).resolve().parent.parent / "core").rglob("*.py")
        offenders = [p.name for p in core_src
                     if "boxes." in p.read_text(encoding="utf-8")
                     and "boxes.wms_design" in p.read_text(encoding="utf-8")]
        assert not offenders, f"core 里引用了 WMS 词表：{offenders}"

    def test_every_model_element_type_can_declare_a_wms_kind(self):
        """**每一类**模型元素都得有 wms_kind——漏一类就有一类需求绑不上

        这个坑踩了五次：先漏了 metric（「拣货准确率 99.5%」绑不上），
        再漏了 process（「收货后按库位策略上架」绑不上）。每次都是
        「判据假定了需求的形状」。

        守卫盯的是**不变量**（每类都有 wms_kind），不是某一个实例——
        再加一类需求类型时漏了字段，这里立刻转红。
        """
        schema = get_definition().get_schema("schema_model_elements")
        by_name = {f.name: f for f in schema.fields}
        # 规则用的是 wms_constraint 而不是 wms_kind：一条规则约束的是
        # **业务约束**（先进先出 / 库位占用唯一），不是某个实体概念。
        # 所以守卫查「有没有 WMS 绑定字段」，不是死盯 wms_kind 这一个名字。
        expected = {
            "entities": "wms_kind",
            "rules": "wms_constraint",
            "processes": "wms_kind",
            "metrics": "wms_kind",
            "events": "wms_kind",
        }
        for arr, field in expected.items():
            assert arr in by_name, f"model_elements 少了 {arr}"
            props = {p.name for p in by_name[arr].items.properties}
            assert field in props, (
                f"{arr} 没有 {field} —— 这一类需求的 WMS 归属绑不上，"
                f"wms_binding 会把它误判成「非 WMS 需求」"
            )

    def test_the_two_wms_rules_are_declared_not_just_computed(self):
        """判据得**声明**在 contract 里，否则算出来的值没人读"""
        metrics = [a.metric for a in get_definition().result.acceptance]
        assert "wms_binding" in metrics
        assert "wms_rule_binding" in metrics

    def test_an_off_domain_requirement_gets_rejected_and_named(self):
        """投料错了要打回，而且要**点名是哪几条**

        「用户登录要用密码」混进 WMS 建模盒，说明投料错了，不是质量差一点。
        只给一个布尔值的话，读者知道红了但不知道该踢哪条出去。
        """
        box = get_definition()
        task_run = self._run(_off_domain_requirement_set()["requirements"])
        assert task_run.result["wms_binding"] is False
        named = task_run.result["evidence"]["evidence_detail"][
            "requirements_without_wms_binding"]
        assert named == ["req_off_1"], f"该点名的是 req_off_1，实际 {named}"
        assert evaluate_acceptance(task_run, box.result).status == AcceptanceStatus.REJECTED

    def test_no_rule_requirement_means_not_applicable_not_failed(self):
        """一批需求里一条 rule 都没有 → 这条判据**用不上**，不是「没过」

        「投料本来就不讲规则」和「模型漏了 WMS 规则」是两件事。
        混为一谈的话，任何只讲实体和指标的需求集都被判红——
        那是判据假定了「每批需求都该有规则」。
        """
        box = get_definition()
        task_run = self._run([
            {"requirement_id": "r_obj", "description": "库存按 SKU 和库位记账",
             "requirement_type": "object", "priority": "must_have"},
            {"requirement_id": "r_met", "description": "拣货准确率要达到 99.5%",
             "requirement_type": "metric", "priority": "must_have"},
        ])
        assert task_run.result["wms_rule_binding"] == "n/a", (
            "没有 rule 需求时 wms_rule_binding 应该是 n/a，"
            "判红等于说「你该有规则」——可你没要求过规则"
        )
        assert evaluate_acceptance(task_run, box.result).status == AcceptanceStatus.ACCEPTED

    def test_the_wms_summary_is_actually_carried_out(self):
        """判了就要看得见——`wms_domain` 不带出来的话，看板上只有红叉"""
        task_run = self._run(_sample_requirement_set()["requirements"])
        w = task_run.result["wms_domain"]
        assert w["domain"] == "WMS"
        assert w["bound_requirement_count"] == w["modelable_requirement_count"]
        assert {e["kind"] for e in w["entities"]} >= {"sku", "lot"}
        assert {c["kind"] for c in w["constraints"]} >= {"unique_slot"}
        # 每条都得带中文标签：读者看的是「库位占用唯一」，不是 unique_slot
        assert all(e["label"] for e in w["entities"])
        assert all(c["statement"] for c in w["constraints"])
