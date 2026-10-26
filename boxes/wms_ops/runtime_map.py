"""
boxes.wms_ops.runtime_map - WMS 域知识：**生产上怎么读一个概念**

**为什么这张表住在运维盒里**：测试盒的探针要造单据（改数据），
所以它在生产上只能判 indeterminate——「现场没条件」。
运维盒是**唯一读得到生产数据**的盒子：它只读，不改。
于是「临期 30 天内 SKU 要能在库存看板看到」这条要求，
只有在这张表的指引下才能在生产上被真正验一次。

**判定口径**：概念 → (端点, 字段)。
字段在生产响应里**存在且非 null** → pass（看板真的报了这个数）；
字段不存在 → fail（看板没报这个数——比如新加的指标还没部署上去）；
端点读不到 → indeterminate（现场没条件，不是「没满足」）。

**这张表是「怎么读」，不是「读到什么算好」**：
后者的口径在业务需求里（statement / evidence），这里只管读法。
"""

from __future__ import annotations

from typing import Any, Optional

# 概念 → 生产上怎么读
#
# field=None 表示「端点答上来了就算 pass」（看板本身在报业务数）
WMS_PRODUCTION_OBSERVATIONS: dict[str, dict[str, Any]] = {
    "wms_expiring_soon_sku_count": {
        "endpoint": "/reports/dashboard",
        "field": "expiringSoonSkuCount",
        "label": "看板上 30 天内到期的 SKU 数",
    },
    "wms_pick_accuracy": {
        "endpoint": "/reports/dashboard",
        "field": "pickAccuracy",
        "label": "看板上的拣货准确率",
    },
    "wms_space_utilization": {
        "endpoint": "/reports/dashboard",
        "field": "spaceUtilization",
        "label": "看板上的库容利用率",
    },
    "wms_stockout_rate": {
        "endpoint": "/reports/dashboard",
        "field": "stockoutRate",
        "label": "看板上的缺货率",
    },
    "wms_inventory_turnover_days": {
        "endpoint": "/reports/dashboard",
        "field": "inventoryTurnoverDays",
        "label": "看板上的库存周转天数",
    },
    "inventory_dashboard": {
        "endpoint": "/reports/dashboard",
        "field": None,
        "label": "库存看板本身在报业务数",
    },
}

# 看板「在报业务数」的判据字段（field=None 的概念用得上）
DASHBOARD_BUSINESS_FIELDS: tuple[str, ...] = (
    "expiringSoonSkuCount",
    "totalOnHandQty",
    "totalAvailableQty",
    "pendingPickTasks",
    "pendingPutawayTasks",
)

# 告警 → 处置类别（**域知识**：WMS 现场真的这么分）
#
# **认不出就标 unclassified，不猜**：把「拣货超时」说成「库存问题」，
# 比说「不知道这是哪一类」更糟——它会让处置走错流程。
WMS_ALERT_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("inventory", ("库存", "效期", "临期", "批次", "呆滞", "盘点")),
    ("outbound", ("拣货", "拣错", "出库", "发货", "波次", "缺货")),
    ("inbound", ("入库", "收货", "上架", "到货", "质检")),
    ("location", ("库位", "容量", "仓容", "占用")),
    ("service", ("服务", "宕", "超时", "5xx", "健康", "连接", "数据库")),
)


def production_read_method(concept: str) -> Optional[dict[str, Any]]:
    """这个概念在生产上怎么读（没有读法 = 不试）"""
    return WMS_PRODUCTION_OBSERVATIONS.get(concept)


def classify_alert(alert: str) -> str:
    """这条告警属于哪一类处置（认不出 → unclassified）"""
    text = (alert or "").lower()
    for category, keywords in WMS_ALERT_CATEGORIES:
        if any(kw.lower() in text for kw in keywords):
            return category
    return "unclassified"
