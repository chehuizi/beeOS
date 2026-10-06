"""
beelines.requirement_capture - 需求捕获履约作业路线

Software Line 第二只 beeline。

业务流：
  input: 自然语言业务表述（BusinessNarrative）
    ↓
  split_source_sentences   （规则：按句末标点 + 顿号枚举切句，每句带 sentence_id）
    ↓
  extract_requirements     （LLM：抽流程图——节点 / 边 / 守卫；不可用则降级规则版）
    ↓
  build_requirement_set    （机械：组装 set_id、编号、绑定 trace_to）
    ↓
  verify_graph_fidelity    （机械：4 项 acceptance——契约 / 覆盖 / 溯源 / 幻觉）
    ↓
  package_requirement_set  （机械：合并成 RequirementSetPackage）
    ↓
  （终态）

**为什么只有 1 个 op 走 LLM**：
"哪句是业务事实、它属于流程图里的哪个位置"需要语义理解；
其余全部是集合运算 / 字符串匹配 / 编号——用模型做只会更慢更不稳。
这条边界是本 beeline 最核心的设计决定。

编排特征：
- 顺序链（无分支）
- 幂等性：所有 op = required（key=set_id，同一份原文重复抽取产出同结果）
"""

from __future__ import annotations

from core.beeline_models import (
    Bee,
    Beeline,
    Idempotency,
    NextRef,
    Operation,
)


IDEMPOTENT_BY_SET = Idempotency(mode="required", key="set_id")


REQUIREMENT_CAPTURE_BEELINE = Beeline(
    id="beeline_requirement_capture_v1",
    version=1,
    description=(
        "需求捕获履约作业路线——把自然语言业务表述抽成可机械验证的"
        "结构化业务需求集（1 个 LLM op + 4 个机械 op）"
    ),

    operations=[
        Operation(
            op_id="split_source_sentences",
            type="source_sentence_splitter",
            input_from="external",
            input="schema_business_narrative",
            output="schema_extracted_facts",
            next=[NextRef(op_id="extract_requirements")],
            bee=Bee(type="source_sentence_splitter_impl"),
            idempotency=IDEMPOTENT_BY_SET,
        ),

        Operation(
            op_id="extract_requirements",
            type="requirement_extractor",
            input_from="split_source_sentences",
            input="schema_extracted_facts",
            output="schema_extracted_facts",
            next=[NextRef(op_id="build_requirement_set")],
            bee=Bee(type="requirement_extractor_impl"),
            idempotency=IDEMPOTENT_BY_SET,
        ),

        Operation(
            op_id="build_requirement_set",
            type="requirement_set_builder",
            input_from="extract_requirements",
            input="schema_extracted_facts",
            output="schema_extracted_facts",
            next=[NextRef(op_id="verify_graph_fidelity")],
            bee=Bee(type="requirement_set_builder_impl"),
            idempotency=IDEMPOTENT_BY_SET,
        ),

        Operation(
            op_id="verify_graph_fidelity",
            type="graph_fidelity_verifier",
            input_from="build_requirement_set",
            input="schema_extracted_facts",
            output="schema_extracted_facts",
            next=[NextRef(op_id="package_requirement_set")],
            bee=Bee(type="graph_fidelity_verifier_impl"),
            idempotency=IDEMPOTENT_BY_SET,
        ),

        Operation(
            op_id="package_requirement_set",
            type="requirement_set_packager",
            input_from="verify_graph_fidelity",
            input="schema_extracted_facts",
            output="schema_requirement_set_package",
            next=[],
            bee=Bee(type="requirement_set_packager_impl"),
            idempotency=IDEMPOTENT_BY_SET,
        ),
    ],
)


def get_beeline() -> Beeline:
    return REQUIREMENT_CAPTURE_BEELINE
