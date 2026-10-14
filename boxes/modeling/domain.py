"""
boxes.modeling.domain - WMS 领域词表 + 机械绑定器

这只盒子是 **WMS 域建模盒**。名字写着 WMS，schema 里就得有 WMS 的概念；
只有名字没有词表，就是「换个招牌的通用盒」——跟这项目反复治的那个病
一个样（判据说覆盖了，实际验的东西跟说的不是一回事）。

**这一层为什么放在 boxes/ 而不是 core/**：WMS 是业务词汇，不是机制词汇。
core 只该知道「实体有个 domain_kind 字段」，不该知道 WMS 有 SKU 和波次。
词表跟盒子走，盒子带得走。

**绑定器是机械的，声明它的盲区**：靠关键词匹配把需求文本绑到 WMS 概念上。
它认不出同义改写（"拣货" vs "按订单取货" vs "order picking"）——
这是**已声明的盲区**，不是没有盲区：绑不上的需求会被 `wms_binding` 判红，
不会静默放过。宁可红，不要假装绑上了。
"""

from __future__ import annotations

import re
from typing import Any

# ============================================================
# WMS 领域实体
# ============================================================
#
# 挑的是 WMS 的**核心对象**，不是「仓库里可能出现的任何名词」。
# 判断标准：这个概念是不是仓库里天天要记账的东西。
WMS_ENTITIES: dict[str, dict[str, Any]] = {
    "sku": {
        "label": "商品",
        "keywords": ["sku", "商品", "货品", "品项", "物料", "编码", "条码", "规格"],
    },
    "owner": {
        "label": "货主",
        "keywords": ["货主", "owner", "供应商", "客户", "委托方", "商家"],
    },
    "warehouse": {
        "label": "仓库",
        "keywords": ["仓库", "仓", "园区", "warehouse", "库区"],
    },
    "location": {
        "label": "库位",
        "keywords": ["库位", "货位", "储位", "location", "巷道", "货架", "地堆"],
    },
    "inventory": {
        "label": "库存",
        "keywords": ["库存", "存量", "inventory", "现货", "可用量", "在库"],
    },
    "inbound": {
        "label": "入库",
        "keywords": ["入库", "收货", "上架", "inbound", "卸货", "验收", "入库单"],
    },
    "outbound": {
        "label": "出库",
        "keywords": ["出库", "发货", "拣货", "复核", "打包", "outbound", "发运", "拣选"],
    },
    "wave": {
        "label": "波次",
        "keywords": ["波次", "wave", "作业组", "批次作业", "任务合并"],
    },
    "lot": {
        "label": "批次",
        "keywords": ["批次", "批号", "效期", "保质期", "生产日期", "lot", "先进先出"],
    },
    "count": {
        "label": "盘点",
        "keywords": ["盘点", "盘盈", "盘亏", "盘点单", "循环盘点", "count"],
    },
}

WMS_ENTITY_KINDS: tuple[str, ...] = tuple(WMS_ENTITIES)

# ============================================================
# WMS 业务约束
# ============================================================
#
# 一条规则只有声明了 WMS 约束才算 WMS 规则。写「当库存大于 0 时…」是通用规则，
# 写「同一 SKU 同库位不可重复占用」才是 WMS 规则——这个区别是判据能验的。
WMS_CONSTRAINTS: dict[str, dict[str, Any]] = {
    "unique_slot": {
        "label": "库位占用唯一",
        "keywords": ["唯一", "不可重复", "重复占用", "独占", "冲突"],
        "statement": "同一 货主 × SKU × 批次 在同一库位不可重复占用",
    },
    "fifo": {
        "label": "先进先出",
        "keywords": ["先进先出", "fifo", "先入先出", "先到先出"],
        "statement": "出库按入库时间升序取货",
    },
    "fefo": {
        "label": "效期先出",
        "keywords": ["效期先出", "fefo", "临期", "先过期"],
        "statement": "出库按批次失效日期升序取货",
    },
    "safety_stock": {
        "label": "安全库存",
        "keywords": ["安全库存", "下限", "补货", "再订货点", "safety"],
        "statement": "库存低于安全下限时触发补货",
    },
    "capacity": {
        "label": "库容上限",
        "keywords": ["库容", "容量", "上限", "满仓", "爆仓", "利用率"],
        "statement": "库位容量上限，超限不得上架",
    },
    "frozen": {
        "label": "冻结不可出",
        "keywords": ["冻结", "质检", "不合格", "封存", "禁用"],
        "statement": "质检未通过的批次冻结，不得出库",
    },
    "wave_merge": {
        "label": "波次合并",
        "keywords": ["波次", "合并", "组波", "拆波", "同时效", "温层"],
        "statement": "同 货主 × 温层 × 时窗 的订单可合并为一个波次",
    },
}

WMS_CONSTRAINT_KINDS: tuple[str, ...] = tuple(WMS_CONSTRAINTS)

# ============================================================
# WMS 指标
# ============================================================
#
# 指标名 + 方向。这四个是仓储现场真的每天看的数，
# 不是「模型里定义了指标」这种自证。
WMS_METRICS: tuple[dict[str, str], ...] = (
    {
        "name": "wms_pick_accuracy",
        "label": "拣货准确率",
        "definition": "一次拣对的数量 ÷ 拣货总数量",
        "target": 99.5,
        "direction": "higher_is_better",
    },
    {
        "name": "wms_inventory_turnover_days",
        "label": "库存周转天数",
        "definition": "平均在库天数（越低越健康）",
        "target": 30.0,
        "direction": "lower_is_better",
    },
    {
        "name": "wms_stockout_rate",
        "label": "缺货率",
        "definition": "发生缺货的 SKU 数 ÷ 在售 SKU 数",
        "target": 1.0,
        "direction": "lower_is_better",
    },
    {
        "name": "wms_space_utilization",
        "label": "库容利用率",
        "definition": "已占用库位 ÷ 可用库位",
        "target": 85.0,
        "direction": "higher_is_better",
    },
)


def _text_of(value: Any) -> str:
    """把一段任意输入压成可匹配的文本（空值返回空串，不猜）"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_text_of(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_text_of(v) for v in value)
    return str(value)


def bind_entity_kind(*parts: Any) -> str | None:
    """这段需求讲的是哪个 WMS 实体？认不出返回 None（**不猜**）

    纯机械的关键词匹配，没有 LLM：绑定这一步要可复现、可回退验证，
    而且「认不出」必须是个能验的状态，而不是变成一个看起来很像对的错。

    认不出的需求会落到 `wms_binding` 的红里——这是设计，不是漏网。
    """
    text = _text_of(parts).lower()
    if not text.strip():
        return None
    for kind, spec in WMS_ENTITIES.items():
        for kw in spec["keywords"]:
            if kw.lower() in text:
                return kind
    return None


# WMS 指标的匹配词。**指标需求也得能绑**——「拣货准确率要达到 99.5%」
# 显然是 WMS 需求，只因为它是一条 metric 类型就不绑，是判据自己
# 假定了「需求只有实体和规则两种形状」。同一个病第七次。
WMS_METRIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "wms_pick_accuracy": ("拣货准确", "拣货错误", "错拣", "拣错", "pick accuracy"),
    "wms_inventory_turnover_days": ("周转天数", "库存周转", "呆滞", "滞销", "turnover"),
    "wms_stockout_rate": ("缺货", "断货", "stockout", "缺货率"),
    "wms_space_utilization": ("库容利用率", "库容", "空间利用率", "利用率"),
}


def bind_metric(*parts: Any) -> str | None:
    """这条需求说的是哪个 WMS 指标？认不出返回 None"""
    text = _text_of(parts).lower()
    if not text.strip():
        return None
    for name, keywords in WMS_METRIC_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text:
                return name
    return None


def bind_constraint(*parts: Any) -> str | None:
    """这条规则约束的是哪条 WMS 业务约束？认不出返回 None

    同样纯机械。**「没有 WMS 约束的规则不算 WMS 规则」**——
    这条差别是判据能验的：数一下 rules 里有多少条声明了 wms_constraint。
    """
    text = _text_of(parts).lower()
    if not text.strip():
        return None
    for kind, spec in WMS_CONSTRAINTS.items():
        for kw in spec["keywords"]:
            if kw.lower() in text:
                return kind
    return None


def label_of(kind: str | None, table: dict[str, dict[str, Any]]) -> str:
    """词表查中文名。查不到返回原样——不拿兜底文案冒充真名"""
    if not kind:
        return ""
    return str(table.get(kind, {}).get("label", kind))


_SENTENCE_SPLIT = re.compile(r"[。；;\n]")


def requirement_sentences(description: str) -> list[str]:
    """把需求描述切成句子——一条需求常常讲了不止一件事

    例：「同一 SKU 在同一库位不可重复占用；库存低于 5 时触发补货」
    这一条需求里藏了两条 WMS 规则。整句匹配只能认出一个。
    按中英文句读符切一刀，只是为了**不漏**，不是为了切得更聪明。
    """
    return [s for s in (x.strip() for x in _SENTENCE_SPLIT.split(description or "")) if s]
