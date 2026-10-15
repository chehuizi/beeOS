"""
core.models - beeBox Definition 顶层数据结构

按 [beebox-design.md §3.1 BeeBox definition] 定义：
  definition = 基础元信息 + 数据形状 + Fulfillment 5 维度 + 度量
  其中 task[].beeline_id + version 是机制层（履约程序 Procedure）
"""

from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, model_validator

# 媒体字段默认接受的 MIME 类型。
#
# 取交集：浏览器能解码出预览 ∩ 视觉接口接受 data URL。
# HEIC / AVIF 不在其中——Chrome 解不了 HEIC（预览直接是破图），
# 接口也拒（实测 400 invalid param: image data url media type
# ["image/heic"] not supported）。两头都不认的东西，放进来只会晚失败。
DEFAULT_MEDIA_ACCEPT: list[str] = [
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
]

# 判据的「不适用」哨兵。
#
# 为什么不能拿 None 顶：null 在这套系统里已经有一个明确的含义了——
# **模型没读到这一格**（小票上的 subtotal 读不出来就是 null，而且这是
# 个真问题，判据该红）。要是再用 null 表示「这张票压根没有这一栏」，
# 两个意思完全相反的状态就挤进同一个值里，谁也分不清。
#
# 实测踩坑（连续两轮，都是同一个病根——把某类票的形状当成所有票的形状）：
#   1. 零售 POS 小票是含税价，没有税额栏 → 「小计+税=实付」判据恒红
#   2. 餐馆结账单只印「品项/数量/金额」，没有单价栏 → 「数量×单价=金额」恒红
# 两次都是判据适用性问题，不是产出有问题。伪装成「通过」是撒谎（盖住症状），
# 硬判成「失败」也是撒谎（把用不上说成做错了）。所以给它第三个状态。
NOT_APPLICABLE = "n/a"


def is_not_applicable(value: Any) -> bool:
    """这个判据值是不是「不适用」"""
    return value == NOT_APPLICABLE

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
    type: str  # string / number / boolean / integer / object / array / enum / ref:<schema_id>
    required: bool = True
    description: Optional[str] = None
    # object 类型用 properties 嵌套
    properties: Optional[list["FieldDef"]] = None
    # array 类型用 items 描述元素
    items: Optional["FieldDef"] = None
    # enum 类型的合法取值表；不声明表示"暂不约束取值"（只校验类型）
    values: Optional[list[Any]] = None
    # 非空 = 这是个媒体字段，取值是 data URL / http URL。
    # 声明式，不靠字段名猜（photo / image / picture …）——
    # 按名字猜等于把业务约定写进内核，换个盒子就失效。
    # 投料口据此把文本框换成图片上传。
    media: Optional[Literal["image"]] = None
    # 媒体字段接受哪些 MIME 类型。不声明就用 DEFAULT_MEDIA_ACCEPT。
    #
    # 为什么要显式收窄：`image/*` 是个**浏览器**的说法，不是「能解码、
    # 能发出去」的说法。iPhone 拍的 .HEIC 在浏览器里 MIME 是 image/heic，
    # 放行 `image/*` 就一路带到 API 才被 400 拒（invalid param: image data
    # url media type ["image/heic"] not supported），用户等一轮才看到一句
    # 读不懂的 JSON。收窄在投料口拦，才对得起「投进来之前就说清楚」。
    media_accept: Optional[list[str]] = None

    def accepted_media_types(self) -> list[str]:
        """这个媒体字段实际接受的 MIME 类型（没声明就用平台默认集）"""
        return self.media_accept or DEFAULT_MEDIA_ACCEPT

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
    """履约合同（Contract）：验收标准（单次判据）

    metric 的取值可以是 bool，也可以是 NOT_APPLICABLE（"n/a"）表示
    **这条判据对本次产出用不上**——比如票上根本没印单价，却要求验
    「数量×单价=金额」。n/a 不算失败，但也**不算通过**：看板会把两者
    分开显示，免得「5 条过 + 1 条用不上」被读成「6 条全过」。
    """

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

    # 这只盒子的腿实现到哪一步了。
    #
    # **为什么要有这个**：`evaluate_acceptance` 里是 `if not failed: ACCEPTED`。
    # 所以一只**零条判据**的盒子会被判成「全过」——那就是
    # 「看起来成功，其实什么也没验」的谎。加骨架盒时最容易踩这个坑。
    #
    # 于是骨架盒的做法反过来：**真声明判据**（它将来要验的东西），
    # 产出**不带**这些 metric → 全部判红 → REJECTED。
    # 「交不出东西」本身就是诚实的信号。
    #
    # 但「未实现」和「实现了但不达标」在看板上必须分得开，而且
    # 骨架盒**不该拖低整体验收率**——那跟拿开发期调试记录算验收率
    # 是同一个病。所以成熟度写在盒子上，由看板和聚合读它。
    maturity: Literal["live", "skeleton"] = Field(
        default="live",
        description=(
            "live = 腿已实现，acceptance 的读数有意义；"
            "skeleton = 结构已立、判据已声明、腿还没实现，"
            "读数不代表能力，不计入验收率"
        ),
    )

    # 产出的展示形态。盒子自己声明它的产出长什么样，而不是让看板去猜
    # （以前靠「result 里有没有 nodes 字段」猜，猜错就把一只盒子的产出
    #  渲染成另一只盒子的形状，看起来像渲染成功，其实是彻底错读）。
    # generic = 声明成通用 JSON 视图：没注册渲染器的产出落到这里，
    # 至少如实呈现，而不是掉进某个不相关的专用视图里。
    view: str = "generic"


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
    # 单位是声明的一部分，不是渲染时的约定：target 和实测 actual 共用同一个 unit，
    # 看板就不可能把「98.0%」和「98.0 秒」画成一样的东西。
    # pct = 百分数（0~100），s = 秒，count = 次。
    unit: Literal["pct", "s", "count"] = "pct"
    # 方向也是声明，不能从 unit 反推：同样是 pct，达成率越高越好，
    # 失败率越低越好。从单位猜方向会让「失败率 0%」被误判成没达标。
    direction: Literal["higher_is_better", "lower_is_better"] = "higher_is_better"


class MetricsDef(BaseModel):
    quality: list[MetricDef] = Field(default_factory=list)
    latency: list[MetricDef] = Field(default_factory=list)
    cost: list[MetricDef] = Field(default_factory=list)


class PlacementDef(BaseModel):
    """盒子在企业里站在哪

    **为什么这部分是 beeBox 自带的，不住在看板里**：盒子要能作为独立
    单元被描述和调用，调用方就得能知道它接谁、投给谁。这些是**这只盒子
    是什么**的一部分，不是「看板长什么样」。

    之前它们住在 `kanban/trigger.py` 的 BOX_META 里，后果是：盒子只能通过
    看板去知道自己接谁，换掉看板接线就丢了——而接线恰恰是描述的一部分。
    「盒子之间的关系是业务信息」这句注释本来就写在 BOX_META 头上，
    它自己承认了这件事，只是放错了地方。

    这里只定义**形状**，跟 metrics 同一个套路：`core` 知道字段叫什么，
    `boxes/` 声明具体值。`core` 里不该出现「费用报销」这种业务值——
    那属于 `boxes/`。

    **feeds_into 是接线的唯一声明，fed_by 不另写**：一边声明「投给谁」，
    另一边从注册表反推。两侧各写一遍的话，A 改了 B 忘了，
    B 的页面上游就悄悄空了，而没有任何东西会报错。
    反过来算则结构上不可能不一致。
    """

    display_name: str = Field(default="", description="给人看的名字")
    value_stream: str = Field(default="", description="所属企业价值流")
    role: str = Field(default="", description="业务责任单元的职责")
    domain: str = Field(
        default="",
        description=(
            "所属业务领域（如 WMS）。**跟 value_stream 是两个维度**："
            "value_stream 答「在价值流哪一段」，domain 答「属于哪个业务领域」。"
            "一只盒子可以既在软件交付价值流、又属于 WMS 域。"
            "域的成员由各盒自己声明派生，不另立一张域表——"
            "那正是 BOX_META 当年被拆掉的原因。"
        ),
    )
    feeds_into: list[str] = Field(
        default_factory=list, description="本盒产出投给哪些 box_id（接线唯一声明）"
    )


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
    - 业务归属 Placement（接谁 / 投给谁，见 PlacementDef）

    机制层独立：
    - task[].beeline_id + version（不作为顶层独立字段）

    **placement 归盒子自己**：看板读它，不拥有它。
    """

    id: str = Field(description="definition 唯一标识")
    version: int = Field(description="definition 版本（递增序列号）")
    description: str

    schemas: list[SchemaDef] = Field(default_factory=list)
    task: list[TaskDef]
    result: ResultDef
    queen: QueenDef
    metrics: MetricsDef
    placement: PlacementDef = Field(default_factory=PlacementDef)

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