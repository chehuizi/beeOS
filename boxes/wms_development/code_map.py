"""
boxes.wms_development.code_map - WMS 概念 → 该概念在 WMS 仓里的代码位置

**这张表为什么存在**：设计盒给的是「业务概念 + 可判定预期」
（fefo、lot、wms_expiring_soon_sku_count…），不是文件路径。
但开发盒要按方案改代码，就得知道「效期先出」这件事在代码里落在哪几处。

没有这张表，开发盒只能有两种做法：
- 不核对 → 提交改了哪儿都不管，「方案 ↔ 提交」永远对不上
- 让模型猜 → 猜出来的路径一会儿对一会儿不对，判据就成了摆设

所以这张表是**域知识**（WMS 的概念与 WMS 的代码布局的对应关系），
住在开发盒自己这儿：它是「要改哪些东西」这件事的判据来源。

**「位置」是并集，不是单点**：一个概念常常横跨几处。
效期先出（fefo）的实现在出库分配（InventoryMapper 的分配 SQL），
但它依赖批次效期在收货→上架之间传下去（inbound）——
只映射到一处，就会把「在正确的地方修 bug」判成红。

**片段是路径的一部分，不是完整路径**：用「包含」匹配，
所以 `mapper/ReportMapper` 同时命中 `ReportMapper.java` 和 `ReportMapper.xml`
（一个指标要改 Mapper 接口、Mapper XML 和看板 VO——只写其中一半，
真改代码时就会撞到「改动不在范围内」）。
"""

from __future__ import annotations

# 概念 → WMS 仓里的源码位置（路径片段）
WMS_CONCEPT_CODE_LOCATIONS: dict[str, tuple[str, ...]] = {
    # ---- 实体 ----
    "sku": ("module/sku", "entity/Sku", "mapper/SkuMapper", "vo/SkuVO"),
    "owner": (),   # WMS 仓里没有货主——这个概念还没有落点（不猜）
    "warehouse": ("module/warehouse", "entity/Warehouse", "mapper/WarehouseMapper",
                  "vo/WarehouseVO"),
    "location": ("module/location", "entity/Location", "mapper/LocationMapper",
                 "vo/LocationVO"),
    "inventory": ("module/stock", "module/inventory", "entity/Inventory",
                  "mapper/InventoryMapper", "vo/InventoryVO"),
    "inbound": ("module/inbound", "entity/InboundOrder", "entity/InboundReceiptItem",
                "entity/PutawayTask", "mapper/InboundOrderMapper",
                "mapper/PutawayTaskMapper"),
    "outbound": ("module/outbound", "entity/OutboundOrder", "entity/PickTask",
                 "mapper/OutboundOrderMapper", "mapper/PickTaskMapper"),
    "wave": ("entity/Wave", "mapper/WaveMapper"),
    "lot": ("entity/InboundReceiptItem", "entity/PutawayTask", "entity/Inventory",
            "module/inbound", "mapper/PutawayTaskMapper"),
    "count": ("module/stocktake", "entity/StocktakeOrder", "mapper/StocktakeMapper"),

    # ---- 约束 ----
    "unique_slot": ("module/location", "mapper/InventoryMapper"),
    "fifo": ("module/outbound", "mapper/InventoryMapper"),
    # **fefo 是并集**：分配在 outbound，但效期得在 inbound 传得下去，
    # 只映射 outbound 会把「在传递链那一侧修 bug」判成红。
    "fefo": ("module/outbound", "mapper/InventoryMapper", "module/inbound",
             "entity/PutawayTask", "entity/InboundReceiptItem"),
    "safety_stock": ("module/sku", "entity/Sku", "mapper/SkuMapper"),
    "capacity": ("module/warehouse", "module/location", "entity/Location"),
    "frozen": ("module/stock", "entity/Inventory", "mapper/InventoryMapper"),
    "wave_merge": ("entity/Wave", "mapper/WaveMapper", "module/outbound"),

    # ---- 指标 ----
    #
    # 指标是**看板上的数**，所以落点在报表模块（SQL + 服务 + VO），
    # 不是「这个业务动作发生的地方」。拣货准确率的数从拣货任务算，
    # 但要让业务方在看板看到它，改的是报表那一侧——
    # 落点表写错的话，真改代码时会被自己的判据判红。
    # **含整个 vo/ 包**：看板上的数要带一个 VO（看板 VO + 它的行 VO），
    # 模型新建 `PickAccuracyRowVO` 是合法改动——上一轮真跑就是被这条卡红的
    # （改动本身是对的，落点表却只写了 vo/DashboardVO）。
    "wms_pick_accuracy": ("module/report", "mapper/ReportMapper", "vo/",
                          "entity/PickTask"),
    "wms_inventory_turnover_days": ("module/report", "mapper/ReportMapper", "vo/"),
    "wms_stockout_rate": ("module/report", "mapper/ReportMapper", "vo/",
                          "module/outbound"),
    "wms_space_utilization": ("module/report", "mapper/ReportMapper", "vo/",
                               "entity/Location"),
    "wms_expiring_soon_sku_count": ("module/report", "mapper/ReportMapper", "vo/"),

    # ---- 报表 ----
    "inventory_dashboard": ("module/report", "mapper/ReportMapper", "vo/"),
}

# 概念 → 该概念在源码里的**实现证据**（标识符片段）
#
# **为什么需要这张表**：「这条需求代码里已经实现了」是一句**自述**。
# 自述不是神谕——上一轮真跑就撞上了：模型看着已有的看板聚合，
# 说「拣货准确率已由 module/report…实现，无需修改」，交了白卷，
# 而源码里根本没有 pickAccuracy 这个数。
#
# 所以「已实现」必须有**能核对的证据**：源码里出现这个片段。
# 片段取**机制**，不是概念名的字面：
# - fefo 的机制是「按 expire_date 排序」（InventoryMapper 的分配 SQL）
#   ——源码里有 `fefo` 这个词吗？没有（那是业务词，不是代码标识符）。
# - wms_pick_accuracy 的机制是「看板上那个字段」→ 字段名 pickAccuracy
#
# **没登记片段的概念 = 判不了**（既不算已实现，也不算待实现）：
# 说不出证据就不能算落地，这跟「认不出就判红」是同一条纪律。
WMS_CONCEPT_IMPL_TOKENS: dict[str, tuple[str, ...]] = {
    # ---- 指标：看板上那个字段名 ----
    "wms_pick_accuracy": ("pickAccuracy",),
    "wms_expiring_soon_sku_count": ("expiringSoonSkuCount",),
    "wms_space_utilization": ("spaceUtilization",),
    "wms_stockout_rate": ("stockoutRate",),
    "wms_inventory_turnover_days": ("inventoryTurnoverDays",),

    # ---- 约束：实现的机制 ----
    "fefo": ("ORDER BY expire_date",),
    "lot": ("expire_date",),

    # ---- 报表 ----
    "inventory_dashboard": ("class DashboardVO",),
}


def implementation_tokens(concept: str) -> tuple[str, ...]:
    """这个概念在源码里的实现证据（没有 = 判不了）"""
    return WMS_CONCEPT_IMPL_TOKENS.get(concept, ())


def implementation_state(concept: str, source_text: str) -> str:
    """这个概念在源码里是「已实现」/「待实现」/「判不了」

    - implemented：登记了证据片段，且片段真的出现在源码里
    - missing：登记了证据片段，但源码里找不到 → **这次要新增**
    - unknown：没登记证据 → 判不了（不许当成已实现）
    """
    tokens = implementation_tokens(concept)
    if not tokens:
        return "unknown"
    if all(tok in source_text for tok in tokens):
        return "implemented"
    return "missing"


def implementation_states(concepts: list[str], source_text: str) -> dict[str, str]:
    """一批概念的实现状态"""
    return {c: implementation_state(c, source_text) for c in concepts}


# 测试目录：改测试永远允许（开发盒要补回归测试）。
# 但**只改测试不算数**——那是「方案落地」的判定，不许靠改测试蒙混。
TEST_PATH_MARKERS: tuple[str, ...] = ("src/test/",)


def locations_for(concepts: list[str]) -> list[str]:
    """这些概念对应的源码位置（去重后的并集）

    没在表里的概念**不贡献任何位置**（不猜）。
    返回空列表 = 这个方案在代码里没有落点。
    """
    out: list[str] = []
    for c in concepts:
        for loc in WMS_CONCEPT_CODE_LOCATIONS.get(c, ()):
            if loc not in out:
                out.append(loc)
    return out


def unmapped_concepts(concepts: list[str]) -> list[str]:
    """这些概念在 WMS 仓里没有对应位置（方案落不了地）"""
    return [c for c in concepts if not WMS_CONCEPT_CODE_LOCATIONS.get(c)]


def is_test_path(path: str) -> bool:
    """这是不是测试文件"""
    return any(m in path for m in TEST_PATH_MARKERS)


def path_in_scope(path: str, allowed: list[str]) -> bool:
    """这个文件在允许改动的范围内吗

    allowed 是概念位置片段（路径的一部分）→ 用「包含」匹配。
    测试文件永远在范围内。
    """
    if is_test_path(path):
        return True
    return any(loc in path for loc in allowed)
