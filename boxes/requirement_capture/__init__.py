"""boxes.requirement_capture - 需求捕获履约盒

自然语言 → 结构化业务需求集（建模盒的输入）。

为什么要独立成盒（而不是建模盒的一个 op）：
- 建模盒的 task in 已经是结构化的 BusinessRequirementSet，它不做自然语言理解
- 这一步的验收判据与建模盒**零重叠**（判据不同 = 值得独立）
- 原先它是 kanban/trigger.py 里的一个影子函数：没有 task_run、失败静默
- 抽出的需求是建模盒的输入，所以建模盒的 schema 归建模盒所有（消费者定契约）

机械可验 4 项 acceptance：
- contract_compliance == true   （需求集过 schema 契约）
- source_coverage      >= 100   （原文每句都被覆盖）
- trace_integrity     == true   （每条需求溯源到真实句子）
- no_hallucination    == true   （需求描述是原文片段，不是编造）

**已知盲区**：type_boundary（rule / metric / process 的边界）无法机械验证——
LLM 实测仍有边界错判。这条走人工确认，不写进 acceptance 假装机器能判。
"""

from boxes.requirement_capture.definition import (
    REQUIREMENT_CAPTURE_BOX,
    get_definition,
)

__all__ = ["REQUIREMENT_CAPTURE_BOX", "get_definition"]
