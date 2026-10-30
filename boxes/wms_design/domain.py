"""
boxes.wms_design.domain - WMS 领域词表 + 机械绑定器

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
        "statement": "库存记账的最小商品单位（库存按 SKU 分别记）",
    },
    "owner": {
        "label": "货主",
        "keywords": ["货主", "owner", "供应商", "客户", "委托方", "商家"],
        "statement": "货主（货权归属方，库存归它所有）",
    },
    "warehouse": {
        "label": "仓库",
        # **「库区」从这里拿走了**：它本来混在仓库的例词里，
        # 结果「库区管理」这类需求会被绑成**仓库**——
        # 绑错了比绑不上更坏（人会被引去改错的东西）。
        # 库区是仓库**下面**的一层分区，是独立实体（见 zone）。
        "keywords": ["仓库", "仓", "园区", "warehouse"],
        "statement": "仓库（存货的物理归属场所）",
    },
    "zone": {
        "label": "库区",
        # **库区 = 仓库下的分区**（A 区整箱、B 区小件、
        # RCV 收货暂存、SHP 发货暂存、DEF 不良品），库位挂在它下面。
        #
        # 以前词表里没有这个实体 → 「增加库区管理功能」这类需求
        # 绑不上任何东西（`wms_binding` 判红），或者更糟：
        # 被绑成「仓库」（因为「库区」当时混在仓库的例词里）。
        "keywords": ["库区", "区域", "分区", "储区", "货区", "zone", "片区"],
        # **归属是两边的事**：
        # - 库位挂在库区上（库位那一侧要校验库区存在且启用）
        # - 库区下还有库位时**不允许删除**（否则库位就成孤儿了）
        #
        # 这句是**库区管理的正确性底线**，不是新增需求：
        # 需求里明明白白写了「库区要能新增、编辑、**删除**、查询」，
        # 而「能删」的前提是「删了不会留下悬空的库位」。
        #
        # **接口契约也写在这儿**——因为它是**三只盒子之间的约定**：
        # 设计盒出预期 → 开发盒按它实现 → 测试盒按它探针。
        # 契约不写下来，三方各写各的：开发盒想叫 /warehouses/zones，
        # 测试盒去调 /zones，探针就只得到一个 404，
        # **「功能没实现」和「接口没对上」混成一个结论**。
        # 形状跟仓里主数据的约定一致（仓库模块就是这套）。
        "statement": (
            "库区（仓库下的分区）。"
            "库位归属在库区上：**用库区编码表达**——"
            "沿用仓里库位已有的 zone 字段（存的是库区编码，不是主键），"
            "存库位时要校验这个库区存在且属于该仓库；"
            "库区下还有库位时不允许删除。"
            "接口按仓库模块的约定："
            "GET /api/v1/zones 查询、"
            "POST /api/v1/zones 新增（warehouseId、code、name）、"
            "PUT /api/v1/zones/{id} 编辑、"
            "DELETE /api/v1/zones/{id} 删除。"
            "**前端也要有管理界面**（跟仓库/库位同一套，"
            "业务方点的是页面，不是接口）："
            "页面 web/src/views/zone/index.vue、"
            "接口封装 web/src/api/zone.ts、"
            "类型 web/src/types/zone.ts、"
            "路由 /zone（在 web/src/router.ts 注册，标题「库区管理」）、"
            "侧边栏主数据菜单里加「库区管理」一项；"
            "页面要能新增、编辑、删除、查询（列表 + 表单）。"
            "**库位已经在用的库区，必须在库区管理里存在**："
            "库区的唯一口径是「**仓库 + 编码**」"
            "（同一个编码出现在两个仓库下，是两条库区记录）；"
            "库位引用了哪个库区，库区管理里就得有那一条"
            "（**新建一个迁移**把库位已用的库区建出来，"
            "不要去改仓里已有的迁移——它们已经发布过了；"
            "不建的话，库位页面编辑任何一条都过不了「库区必须存在」的校验）。"
            "**库位管理页面的「库区」字段要从库区列表里选**，"
            "不能让人手填编码——手填会造出新的孤儿引用"
        ),
    },
    "location": {
        "label": "库位",
        "keywords": ["库位", "货位", "储位", "location", "巷道", "货架", "地堆"],
        "statement": "库位（仓内具体的存放位置，库存记在库位上）",
    },
    "inventory": {
        "label": "库存",
        "keywords": ["库存", "存量", "inventory", "现货", "可用量", "在库"],
        "statement": "库存（货主 × SKU × 库位 上的在库数量）",
    },
    "inbound": {
        "label": "入库",
        "keywords": ["入库", "收货", "上架", "inbound", "卸货", "验收", "入库单"],
        "statement": "入库（收货验收与上架的单据流）",
    },
    "outbound": {
        "label": "出库",
        "keywords": ["出库", "发货", "拣货", "复核", "打包", "outbound", "发运", "拣选"],
        "statement": "出库（拣货复核与发货的单据流）",
    },
    "wave": {
        "label": "波次",
        "keywords": ["波次", "wave", "作业组", "批次作业", "任务合并"],
        "statement": "波次（合并作业的一组出库任务）",
    },
    "lot": {
        "label": "批次",
        "keywords": ["批次", "批号", "效期", "保质期", "生产日期", "lot", "先进先出"],
        "statement": "批次（同 SKU 下按生产日期/失效日期区分的一批货）",
    },
    "count": {
        "label": "盘点",
        "keywords": ["盘点", "盘盈", "盘亏", "盘点单", "循环盘点", "count"],
        "statement": "盘点（对账与盈亏处理）",
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
# WMS 报表 / 看板
# ============================================================
#
# 单独一类，不塞进实体表：实体表的判准是「仓库里天天要记账的东西」，
# 看板不是记账对象，它是一堆指标的呈现面。
#
# **它是词表里本来就该有的一条**——WMS 真的有一个看板
# （线上 /wms/ 的首页，服务端是 `/api/v1/reports/dashboard`），
# 而业务方真的会提它：「临期 30 天以内的 SKU 要能在**库存看板**看到」。
#
# 之前词表里没有它 → 需求盒把这句话拆出的「库存看板」这个片段绑不上任何概念
# → `wms_binding` 判红（7/8 绑上）→ **主线需求在设计盒过不去**。
WMS_REPORTS: dict[str, dict[str, Any]] = {
    "inventory_dashboard": {
        "label": "库存看板",
        "statement": "仓储现场的看板/报表（库存、库位、单据、临期预警）",
        "keywords": ["看板", "报表", "dashboard", "面板", "预警看板"],
        # **这张看板真的报哪些数**（线上 `/api/v1/reports/dashboard` 的字段：
        # expiringSoonSkuCount / totalOnHandQty / totalAvailableQty …）。
        #
        # 这条关系是 WMS 的事实，不是猜的：业务方说
        # 「临期 30 天内 SKU 要能在库存看板看到」，这句话点了两样东西——
        # 一个量（临期 SKU 数）和承载它的面（看板）。
        # 绑定腿只认出「看板」而漏掉那个量时，那条指标就不会变成
        # **可判定的预期**，测试盒就不会去验它——业务价值根本没被验证。
        "carries_metrics": ["wms_expiring_soon_sku_count"],
    },
}

WMS_REPORT_KINDS: tuple[str, ...] = tuple(WMS_REPORTS)

# ============================================================
# WMS 指标
# ============================================================
#
# 指标名 + 方向。这四个是仓储现场真的每天看的数，
# 不是「模型里定义了指标」这种自证。
WMS_METRICS: tuple[dict[str, Any], ...] = (
    {
        "name": "wms_pick_accuracy",
        "label": "拣货准确率",
        "definition": "一次拣对的数量 ÷ 拣货总数量",
        "target": 99.5,
        "direction": "higher_is_better",
        "keywords": ["拣货准确率", "拣货准确", "拣货错误", "错拣", "拣错", "一次拣对", "pick accuracy"],
    },
    {
        "name": "wms_inventory_turnover_days",
        "label": "库存周转天数",
        "definition": "平均在库天数（越低越健康）",
        "target": 30.0,
        "direction": "lower_is_better",
        "keywords": ["库存周转天数", "周转天数", "库存周转", "平均在库天数", "呆滞", "滞销", "turnover"],
    },
    {
        "name": "wms_stockout_rate",
        "label": "缺货率",
        "definition": "发生缺货的 SKU 数 ÷ 在售 SKU 数",
        "target": 1.0,
        "direction": "lower_is_better",
        "keywords": ["缺货率", "缺货", "断货率", "断货", "stockout"],
    },
    {
        "name": "wms_space_utilization",
        "label": "库容利用率",
        "definition": "已占用库位 ÷ 可用库位",
        "target": 85.0,
        "direction": "higher_is_better",
        "keywords": ["库容利用率", "库容", "库位利用率", "空间利用率", "利用率"],
    },
    {
        # **这是词表里本来就该有的一条**——线上 WMS 的 dashboard 真的报这个数
        # （`expiringSoonSkuCount`：30 天内到期且在库的 SKU 数）。
        #
        # 之前没有它 → 「临期 30 天以内的 SKU 要能在看板看到」这种需求
        # 绑不上任何指标 → 模型里没有指标 → `metrics_defined` 判红。
        # 指标表记的是**WMS 真的在盯的数**，不是同义词词典——
        # 所以补它是补一个漏掉的事实，不是打补丁。
        "name": "wms_expiring_soon_sku_count",
        "label": "临期 SKU 数",
        "definition": "在库且 30 天内到期的 SKU 数（临期预警口径）",
        "target": 0.0,
        "direction": "lower_is_better",
        "keywords": ["临期 SKU 数", "临期 SKU", "临期商品", "临期 30 天", "30 天内到期的 SKU", "临期数", "临期预警", "效期预警", "即将过期", "快过期", "临期"],
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
# WMS 指标的匹配词。**从 WMS_METRICS 派生，不再抄一份**——
#
# 以前这里是独立抄的一份：指标表里加了新指标、这张表没跟上，
# 绑定腿的目录里就没有那个指标的说法，LLM 看不见它 → 绑不上 →
# `metrics_defined` 判红，而红的原因离现场隔着两层。
#
# 「同一份形状抄两遍就会漂」——这里让它没法漂。
WMS_METRIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    m["name"]: tuple(m["keywords"]) for m in WMS_METRICS
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


def kind_category(kind: str | None) -> str:
    """这个 WMS 概念是哪一类：entity / constraint / metric / ''（不认识）

    **为什么需要它**：需求盒给的 `requirement_type` 是**位置**推出来的
    （图上的节点→process、边的守卫→rule、节点的 measures→metric），
    不是语义判断。而 WMS 概念的性质（是实体？是约束？是指标？）
    要由**概念本身**决定。

    真 LLM 实测过：业务方说「同一个 SKU 必须先出快过期的批次」，
    需求盒把它放在节点上（于是类型是 process），而设计盒正确地认出了
    `fefo`——**拿位置标签去否决语义判断，就等于把对的答案改成错的**。
    """
    if not kind:
        return ""
    if kind in WMS_ENTITIES:
        return "entity"
    if kind in WMS_CONSTRAINTS:
        return "constraint"
    if kind in WMS_REPORTS:
        return "report"
    if any(m["name"] == kind for m in WMS_METRICS):
        return "metric"
    return ""


def label_of(kind: str | None, table: dict[str, dict[str, Any]]) -> str:
    """词表查中文名。查不到返回原样——不拿兜底文案冒充真名"""
    if not kind:
        return ""
    return str(table.get(kind, {}).get("label", kind))


def label_for_kind(kind: str | None) -> str:
    """WMS 概念的中文名（**跨四类词表查**）

    以前只看实体表和约束表 → 指标（`wms_expiring_soon_sku_count`）
    和报表（`inventory_dashboard`）查不到，label 直接退回成英文 id，
    看板上就出现一串机器名，而不是「临期 SKU 数」「库存看板」。
    """
    if not kind:
        return ""
    if kind in WMS_ENTITIES:
        return label_of(kind, WMS_ENTITIES)
    if kind in WMS_CONSTRAINTS:
        return label_of(kind, WMS_CONSTRAINTS)
    if kind in WMS_REPORTS:
        return label_of(kind, WMS_REPORTS)
    for m in WMS_METRICS:
        if m["name"] == kind:
            return str(m.get("label", kind))
    return kind


def statement_for_kind(kind: str | None) -> str:
    """WMS 概念的正式表述（报表/约束/指标/实体都有）

    **四类词表全都要查**——漏掉一类，那一类的预期就带着一句空表述，
    下游拿着它对不上任何东西（而看板上看不出来，只是一句空话）。
    """
    if not kind:
        return ""
    if kind in WMS_ENTITIES:
        return str(WMS_ENTITIES[kind].get("statement", ""))
    if kind in WMS_CONSTRAINTS:
        return str(WMS_CONSTRAINTS[kind].get("statement", ""))
    if kind in WMS_REPORTS:
        return str(WMS_REPORTS[kind].get("statement", ""))
    for m in WMS_METRICS:
        if m["name"] == kind:
            return str(m.get("definition", ""))
    return ""


_SENTENCE_SPLIT = re.compile(r"[。；;\n]")


def requirement_sentences(description: str) -> list[str]:
    """把需求描述切成句子——一条需求常常讲了不止一件事

    例：「同一 SKU 在同一库位不可重复占用；库存低于 5 时触发补货」
    这一条需求里藏了两条 WMS 规则。整句匹配只能认出一个。
    按中英文句读符切一刀，只是为了**不漏**，不是为了切得更聪明。
    """
    return [s for s in (x.strip() for x in _SENTENCE_SPLIT.split(description or "")) if s]
