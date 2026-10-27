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

from typing import Optional

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

# 概念 → 该概念在源码里的**实现证据**（文件落点提示 + 标识符片段）
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
#
# **为什么证据还要带「在哪个文件里」**（2026-10-27 实测撞出来的）：
# 只查「全仓有没有这个字符串」会被**同名方法**骗过去——
# 上一轮开发盒把拣货准确率做成了独立接口 `/reports/pick-accuracy`，
# 里面有个方法叫 `pickAccuracy()`，于是「全仓有 pickAccuracy」成立，
# 开发盒判「已实现」就放行了。可业务方要的是
# **「在库存看板看得到」**——那个字段在 `DashboardVO` 里，
# 而它压根不在看板 VO 上。**字段在别处 = 业务方看不到。**
#
# 所以证据改成 (文件落点提示, 标识符片段)：
# - 片段必须出现在**指定的那个文件**里才算数；
# - 报表类指标的落点就是看板 VO（`vo/DashboardVO`）——
#   看板接口返回的就是它，字段不在那儿 = 业务方看不到。
#
# **没登记证据的概念 = 判不了**（既不算已实现，也不算待实现）：
# 说不出证据就不能算落地，这跟「认不出就判红」是同一条纪律。
WMS_CONCEPT_IMPL_EVIDENCE: dict[str, tuple[tuple[str, str], ...]] = {
    # ---- 指标：必须是**看板 VO 上的那个字段** ----
    "wms_pick_accuracy": (("vo/DashboardVO", "pickAccuracy"),),
    "wms_expiring_soon_sku_count": (("vo/DashboardVO", "expiringSoonSkuCount"),),
    "wms_space_utilization": (("vo/DashboardVO", "spaceUtilization"),),
    "wms_stockout_rate": (("vo/DashboardVO", "stockoutRate"),),
    "wms_inventory_turnover_days": (("vo/DashboardVO", "inventoryTurnoverDays"),),

    # ---- 约束：实现的机制 ----
    "fefo": (("mapper/InventoryMapper", "ORDER BY expire_date"),),
    "lot": (("mapper/InventoryMapper", "expire_date"),),

    # ---- 报表 ----
    "inventory_dashboard": (("vo/DashboardVO", "class DashboardVO"),),
}


def implementation_tokens(concept: str) -> tuple[str, ...]:
    """这个概念在源码里的实现证据（标识符片段；没有 = 判不了）"""
    return tuple(tok for _hint, tok in implementation_evidence(concept))


def implementation_evidence_description(concept: str) -> str:
    """这个概念的证据长什么样（给人看的：哪个文件里的哪个标识符）

    「vo/DashboardVO 里的 pickAccuracy」——模型得知道**落在哪个文件**，
    否则它会在别处造一个同名的东西，机械层照样判不了它落地。
    """
    parts = [
        (f"{hint} 里的 {tok}" if hint else tok)
        for hint, tok in implementation_evidence(concept)
    ]
    return "、".join(parts)


def implementation_evidence(concept: str) -> tuple[tuple[str, str], ...]:
    """这个概念的证据（文件落点提示 + 标识符片段）"""
    return WMS_CONCEPT_IMPL_EVIDENCE.get(concept, ())


def implementation_state(
    concept: str,
    source_text: str = "",
    files: Optional[dict[str, str]] = None,
) -> str:
    """这个概念在源码里是「已实现」/「待实现」/「判不了」

    - implemented：登记了证据，且证据**真的出现在指定的那个文件里**
    - missing：登记了证据，但源码里找不到 → **这次要新增**
    - unknown：没登记证据 → 判不了（不许当成已实现）

    `files` 是 {相对路径: 源码}。**给了它才谈得上「在哪个文件里」**：
    只给一段拼接文本的话，只能做全文匹配——那就分不出
    「看板上有这个字段」和「别处有个同名方法」。

    没给 `files` 时退回全文匹配（老调用方的口径），
    但**带文件提示的证据在全文模式下不算数**——
    宁可是「待实现」（逼它真去改），也不要「已实现」放过一个假实现。
    """
    evidence = implementation_evidence(concept)
    if not evidence:
        return "unknown"
    if files is None:
        # 没有文件视图：带文件提示的证据判不了 → 记成「待实现」。
        if any(hint for hint, _tok in evidence):
            return "missing"
        return (
            "implemented"
            if all(tok in source_text for _hint, tok in evidence)
            else "missing"
        )
    for hint, tok in evidence:
        if not any(hint in path and tok in text for path, text in files.items()):
            return "missing"
    return "implemented"


def implementation_states(
    concepts: list[str],
    source_text: str = "",
    files: Optional[dict[str, str]] = None,
) -> dict[str, str]:
    """一批概念的实现状态"""
    return {c: implementation_state(c, source_text, files) for c in concepts}


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
