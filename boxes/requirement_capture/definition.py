"""
boxes.requirement_capture.definition - 需求捕获履约盒 Definition 数据

按 Fulfillment 5 维度 + 度量组织：
- 基础元信息
- 数据形状（schemas）
- 履约意图 Intent（task）
- 履约合同 Contract（result + 4 条机械 acceptance + 2 条 exception）
- 履约政策 Policy（queen authorization + rules）
- 履约度量 Metrics（每个指标都有实测采集算法，见 measure.py）

为什么这 4 条 acceptance 能做到 0 主观：
1. contract_compliance —— 复用 runtime.contract.validate_payload，形状校验是纯机械的
2. source_coverage     —— 原文切句后，每句要么是 goal、要么被至少一条需求覆盖（集合运算）
3. trace_integrity     —— 每条需求的 trace_to 必须指向真实存在的 sentence_id
4. no_hallucination    —— 每条需求的 description 必须在原文里找得到（子串匹配）

**已知盲区（诚实声明）**：type_boundary（rule vs metric vs process 的边界）
LLM 实测仍有 2/10 错，这条无法机械验证——因此在看板投料口做人工确认
（human-in-the-loop），不写进 acceptance 假装机器能判。
"""

from __future__ import annotations

from core.models import (
    AcceptanceRule,
    BeeBoxDefinition,
    ExceptionRule,
    MetricDef,
    MetricsDef,
    QueenAuthorization,
    QueenDef,
    QueenRules,
    ResultDef,
    TaskDef,
)
from boxes.requirement_capture.schemas import ALL_SCHEMAS


REQUIREMENT_CAPTURE_BOX = BeeBoxDefinition(
    id="requirement_capture_box",
    version=1,
    description=(
        "需求捕获履约盒——把自然语言业务表述抽成可机械验证的结构化业务需求集，"
        "每条需求带原文溯源，并交出 4 项抽取证据供建模盒投料"
    ),

    schemas=ALL_SCHEMAS,

    task=[
        TaskDef(
            type="capture_business_requirement",
            task_schema="schema_business_narrative",
            beeline_id="beeline_requirement_capture_v1",
            beeline_version=1,
            trigger=(
                "业务方用自然语言描述业务\n"
                "OR 产品经理要一份可投料的结构化需求集\n"
                "OR 上一轮抽取有需求被人工改回原文重抽"
            ),
        ),
    ],

    result=ResultDef(
        type="business_requirement_captured",
        result_schema="schema_requirement_set_package",
        view="flow_graph",
        description="结构化业务需求集 + 4 项抽取验证证据",

        acceptance=[
            AcceptanceRule(metric="contract_compliance", op="eq", value=True),
            AcceptanceRule(metric="source_coverage", op="gte", value=100.0),
            AcceptanceRule(metric="trace_integrity", op="eq", value=True),
            AcceptanceRule(metric="no_hallucination", op="eq", value=True),
        ],

        exceptions=[
            # 抽不出任何需求 = 业务方表述太笼统，别硬编
            ExceptionRule(
                condition="requirement_count == 0",
                action="no_deliver",
                reason="原文里没识别出可建模的业务事实",
            ),
            # 大段原文没被任何需求覆盖 = 抽取漏了
            ExceptionRule(
                condition="source_coverage < 50",
                action="escalate",
                reason="一半以上原文没被抽取到，需要业务方澄清",
            ),
        ],
    ),

    queen=QueenDef(
        authorization=QueenAuthorization(
            task_routing="allow",
            exception_handling="allow",
            # 抽取质量会持续变化，但改善动作先只观察——
            # 真实系统里"让 queen 自己改进抽取"风险太高（可能悄悄改变业务语义）
            continuous_improvement="observe_only",
        ),
        rules=QueenRules(
            task_routing={
                # 原文越长越容易抽漏，超过 20 句先建议拆批
                "narrative_length_thresholds": {
                    "short": 20,    # <= 20 句直接抽取
                    "long": 50,     # 20-50 句抽取后强制人工复核
                    # > 50 句触发 escalate（建议业务方分批描述）
                },
            },
            exception_handling={
                # 抽取质量不达标时 queen 的动作边界：
                # 允许重抽 / 要求澄清；禁止凭空造需求、禁止改写业务目标
                "re_extract_on": ["no_hallucination_failed", "source_coverage_low"],
                "request_clarification_on": ["requirement_count_zero"],
                "forbidden": ["invent_requirements", "rewrite_business_goal"],
            },
            continuous_improvement={
                # 只 observe 真正有信号源的指标：
                # 人工改写率（manual_rewrite_rate）没有人工改写这个事件，测不出来，
                # 已经从 metrics 里拿掉——观察一个算不出来的数是自欺。
                "observe": ["first_pass_capture_rate", "llm_call_failure_rate"],
            },
        ),
    ),

    metrics=MetricsDef(
        quality=[
            MetricDef(
                name="extraction_precision",
                definition="抽出的需求中不 hallucinate 的比例（no_hallucination 通过率）",
                target=98.0,
                unit="pct",
            ),
            MetricDef(
                name="extraction_recall",
                definition="原文事实被抽取覆盖的比例（source_coverage）",
                target=100.0,
                unit="pct",
            ),
            MetricDef(
                name="first_pass_capture_rate",
                definition="首次抽取即通过全部 acceptance 的比例",
                target=85.0,
                unit="pct",
            ),
            MetricDef(
                name="llm_call_failure_rate",
                definition="LLM 调用失败导致履约中止的比例（降级已删，"
                           "没有第二条腿兜着，失败就是失败）",
                target=2.0,
                unit="pct",
                direction="lower_is_better",
            ),
        ],
        latency=[
            MetricDef(
                name="capture_latency",
                definition="抽取端到端时长（秒）",
                target=15.0,
                unit="s",
                direction="lower_is_better",
            ),
        ],
    ),
)


def get_definition() -> BeeBoxDefinition:
    return REQUIREMENT_CAPTURE_BOX
