"""
core.models - beeBox Definition 顶层数据结构

按 [beebox-design.md §3.1 BeeBox definition] 定义：
  definition = 基础元信息 + 数据形状 + Fulfillment 5 维度 + 度量
  其中 task[].beeline_id + version 是机制层（履约程序 Procedure）
"""

from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, model_validator

# ============================================================
# 业务化枚举（跨盒子共享）
# ============================================================


class ExceptionType(str):
    """订单异常类型"""

    INVENTORY_SHORTAGE = "inventory_shortage"
    PAYMENT_FAILED = "payment_failed"
    SHIPPING_DELAY = "shipping_delay"
    CUSTOMER_COMPLAINT = "customer_complaint"
    PARTIAL_UNAVAILABLE = "partial_unavailable"


class Severity(str):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CustomerTier(str):
    STANDARD = "standard"
    VIP = "vip"
    ENTERPRISE = "enterprise"


class ResolutionType(str):
    """异常处理结果类型"""

    FULFILLED = "fulfilled"
    PARTIAL_FULFILLED = "partial_fulfilled"
    REFUNDED = "refunded"
    COMPENSATED = "compensated"
    ESCALATED = "escalated"
    REJECTED = "rejected"


# ============================================================
# 数据形状：Schema 定义（definition 自带）
# ============================================================


class FieldDef(BaseModel):
    """字段定义（递归支持 object / array）"""

    name: str
    type: str  # string / number / boolean / integer / object / array / ref:<schema_id>
    required: bool = True
    description: Optional[str] = None
    # object 类型用 properties 嵌套
    properties: Optional[list["FieldDef"]] = None
    # array 类型用 items 描述元素
    items: Optional["FieldDef"] = None

    @model_validator(mode="after")
    def validate_type_structure(self) -> "FieldDef":
        if self.type == "object" and not self.properties:
            raise ValueError(f"object type field '{self.name}' must have properties")
        if self.type == "array" and not self.items:
            raise ValueError(f"array type field '{self.name}' must have items")
        return self


class SchemaDef(BaseModel):
    """业务 schema 定义（definition 内唯一）"""

    id: str = Field(description="schema 唯一标识（definition 内唯一）")
    description: Optional[str] = None
    fields: list[FieldDef]


# ============================================================
# Fulfillment 维度 - Intent / Contract / Policy
# ============================================================


class TaskDef(BaseModel):
    """履约意图（Intent）：beeBox 接收什么 task"""

    type: str = Field(description="task 类型标识")
    task_schema: str = Field(description="引用 schemas 块内的 schema id")
    beeline_id: str = Field(description="1:1 绑定的 beeline（履约程序 Procedure 实现）")
    beeline_version: int = Field(description="绑定的 beeline 具体 version")
    trigger: Optional[str] = Field(default=None, description="触发条件描述（业务化）")


class AcceptanceRule(BaseModel):
    """履约合同（Contract）：验收标准（单次判据）"""

    metric: str
    op: Literal["gte", "lte", "eq", "in", "match"]
    value: Any


class ExceptionRule(BaseModel):
    """履约合同（Contract）：例外条款"""

    condition: str
    action: Literal["no_deliver", "rollback", "escalate"]


class ResultDef(BaseModel):
    """履约合同（Contract）：业务结果定义"""

    type: str = Field(description="业务结果类型标识")
    result_schema: str = Field(description="引用 schemas 块内的 schema id")
    acceptance: list[AcceptanceRule]
    exceptions: list[ExceptionRule]


class QueenAuthorization(BaseModel):
    """履约政策（Policy）：queen 自治授权档位"""

    task_routing: Literal["allow", "observe_only", "off"] = "allow"
    exception_handling: Literal["allow", "observe_only", "off"] = "allow"
    continuous_improvement: Literal["allow", "observe_only", "off"] = "observe_only"


class QueenRules(BaseModel):
    """履约政策（Policy）：queen 自治规则（业务化规则，可选）"""

    task_routing: Optional[dict[str, Any]] = None
    exception_handling: Optional[dict[str, Any]] = None
    continuous_improvement: Optional[dict[str, Any]] = None


class QueenDef(BaseModel):
    """履约政策（Policy）：beeBox queen 自治配置"""

    authorization: QueenAuthorization
    rules: QueenRules


# ============================================================
# 履约度量（Metrics）
# ============================================================


class MetricDef(BaseModel):
    """度量定义（持续统计）"""

    name: str
    definition: str
    target: float


class MetricsDef(BaseModel):
    quality: list[MetricDef] = Field(default_factory=list)
    latency: list[MetricDef] = Field(default_factory=list)
    cost: list[MetricDef] = Field(default_factory=list)


# ============================================================
# 顶层：BeeBox Definition
# ============================================================


class BeeBoxDefinition(BaseModel):
    """beeBox definition 顶层结构

    按 Fulfillment 5 维度 + 度量组织：
    - 基础元信息（id / version / description）
    - 数据形状（schemas）
    - 履约意图 Intent（task 列表）
    - 履约合同 Contract（result）
    - 履约政策 Policy（queen）
    - 履约度量 Metrics

    机制层独立：
    - task[].beeline_id + version（不作为顶层独立字段）
    """

    id: str = Field(description="definition 唯一标识")
    version: int = Field(description="definition 版本（递增序列号）")
    description: str

    schemas: list[SchemaDef] = Field(default_factory=list)
    task: list[TaskDef]
    result: ResultDef
    queen: QueenDef
    metrics: MetricsDef

    def get_schema(self, schema_id: str) -> Optional[SchemaDef]:
        """按 id 查找 schema"""
        for s in self.schemas:
            if s.id == schema_id:
                return s
        return None

    def get_task(self, task_type: str) -> Optional[TaskDef]:
        """按 type 查找 task"""
        for t in self.task:
            if t.type == task_type:
                return t
        return None