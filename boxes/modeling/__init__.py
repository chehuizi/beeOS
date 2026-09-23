"""boxes.modeling - 业务建模履约盒

Software Line 第一只盒子——按结构化需求产出可验证的业务模型包。

业务责任：
- 输入：业务需求集（结构化：每条需求带 id / type / priority）
- 输出：业务模型包（含模型元素 + 机械可验证据）
- 不负责：模型的人工业务方确认（独立阶段，由业务方手动 accept）

机械可验 5 项 acceptance：
- requirement_coverage == 100   （每条需求都有 model 元素对应）
- rule_consistency == true       （规则无矛盾）
- reference_integrity == true    （引用一致）
- structural_compliance == true  （结构合规）
- metrics_defined == true        （指标明确）

业务语义是真的（结构化需求 → 模型元素 + 证据）；
基础设施可以是假的（每个 op 是 mock，下一阶段实现真实 modeling engine）。
"""

from boxes.modeling.definition import BUSINESS_MODELING_BOX, get_definition

__all__ = ["BUSINESS_MODELING_BOX", "get_definition"]