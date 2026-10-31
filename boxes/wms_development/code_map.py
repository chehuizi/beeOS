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

import re
from typing import Any, Optional

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
    # ---- 库区（仓库下的分区，库位归属在它上面）----
    #
    # **这是一个全新模块**（entity/mapper/service/controller/XML/DTO/VO
    # 全都没有），所以落点里除了「新东西要落在哪」，
    # 还得给「**照着谁写**」——新建一个模块时，
    # 模型手上没有任何同类文件可参考，写出来的东西跟仓里的风格必然不像。
    # 这里把仓库模块（最简单的一套主数据 CRUD）列为落点：
    # 它既是参考，也是「改动不许跑偏」的边界。
    #
    # **库位那一侧的文件也在落点里**：「库位归属在库区上」这句话，
    # 实现它就得动库位实体（加库区字段）、库位 Mapper、库位 DTO/VO——
    # 不在落点里的话，模型改了这些文件会被判成「改了方案没覆盖的文件」，
    # 而它改的恰恰是这条需求要的（实测：加了 zoneId 的库位实体被判越界）。
    #
    # ---- 前端那一侧也在落点里 ----
    #
    # **为什么后端做完了还要前端**：业务方点的是**页面**，不是接口。
    # 只有 REST 没有页面 = 功能没交付（用户看到的还是「没有库区管理」）。
    # 落点写成路径片段，所以 `web/src/api/zone` 命中 `web/src/api/zone.ts`。
    #
    # **router.ts / layouts 是共享文件**：改它们会影响别的菜单，
    # 但「加一个菜单项」本来就只能改这两个文件——
    # 把它们写进落点，是让「改这两个文件」变成**方案允许的改动**，
    # 而不是靠模型自觉。
    "zone": ("entity/Zone", "mapper/ZoneMapper", "module/zone", "vo/ZoneVO",
             "dto/Zone", "db/migration", "module/location",
             "entity/Location", "mapper/LocationMapper", "vo/LocationVO",
             "dto/LocationSaveRequest",
             "module/warehouse", "entity/Warehouse", "mapper/WarehouseMapper",
             "vo/WarehouseVO", "dto/WarehouseQuery",
             # ---- 前端 ----
             # **库区页面是新建的** → 模型手上没有任何同类文件可参考，
             # 写出来的东西跟仓里的风格必然不像（后端那边有 module/warehouse
             # 当样板，前端这边就得有对应的）。
             # 仓库页面 = 最简单的一套主数据 CRUD；库位页面 = 跟库区最像
             # （库位要挂在库区上，页面得能选库区）。
             "web/src/views/zone", "web/src/api/zone", "web/src/types/zone",
             "web/src/views/warehouse", "web/src/api/warehouse",
             "web/src/types/warehouse",
             "web/src/views/location", "web/src/api/location",
             "web/src/types/location",
             "web/src/router.ts", "web/src/layouts"),

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
WMS_CONCEPT_IMPL_EVIDENCE: dict[str, tuple[tuple[str, ...], ...]] = {
    # ---- 指标：必须是**看板 VO 上的那个字段** ----
    "wms_pick_accuracy": (("vo/DashboardVO", "pickAccuracy"),),
    "wms_expiring_soon_sku_count": (("vo/DashboardVO", "expiringSoonSkuCount"),),
    "wms_space_utilization": (("vo/DashboardVO", "spaceUtilization"),),
    "wms_stockout_rate": (("vo/DashboardVO", "stockoutRate"),),
    "wms_inventory_turnover_days": (("vo/DashboardVO", "inventoryTurnoverDays"),),

    # ---- 库区：实体 + Mapper + 对外接口 + **库位挂在它上面** ----
    #
    # **为什么四样都要**：只有实体没有接口 = 没人能用它；
    # 只有接口没有 Mapper = 一调就 500。
    # 「库区管理」这个需求要的是**能用的管理功能**，
    # 不是「仓里多了个类」。
    #
    # **为什么还要「两边都引用对方」**（2026-10-28 实测）：
    # 上一轮开发盒把库区做齐了（实体/Mapper/Service/Controller），
    # 可需求里还有一句「**库位归属在库区上**」——
    # 那句话**没有被任何证据覆盖**，于是它既没实现，也**没人发现它没实现**。
    # 判据全绿，交付物里少了一半业务价值。
    #
    # 「归属」是**两边的事**：
    # - 库位这一侧要引用库区（存库位时校验库区存在且启用）
    # - 库区这一侧要看到库位（库区下还有库位时不允许删）
    # 只做一边 = 归属关系是断的。
    "zone": (("entity/Zone", "class Zone"),
             ("mapper/ZoneMapper", "ZoneMapper"),
             ("module/zone", "ZoneController"),
             # 库位这一侧引用库区
             # 库位这一侧：存库位时要校验库区 → 在**服务**里引用库区。
             # 写成「module/location 里出现 Zone」的话，模型把 Zone 实体
             # **复制一份**到 location 包里也就算过了（实测真这么干了：
             # module/location/Zone.java + module/zone/Location.java，
             # 两份重名实体，编译过、单测过，坑留在仓里）。
             ("service/LocationServiceImpl", "Zone", "usage"),
             # 库区这一侧引用库位（删库区前要拦住）
             ("module/zone", "Location"),
             # ---- 前端三件套：封装 / 页面真的用 / 路由真的注册 ----
             #
             # **为什么页面那条要用 ts_usage 而不是 any**：
             # 前端的骗法跟 Java 一样常见——`import { zoneApi } from '@/api/zone'`
             # 然后**一次都没调用**，页面照样能编译通过、构建也不报错。
             # 「只 import 就骗过证据」在 Vue 里是同一族病。
             ("web/src/api/zone", "zoneApi"),
             ("web/src/views/zone", "zoneApi", "ts_usage"),
             # 路由用 **literal** 模式：token 必须被引号**单独**包住。
             # 这样 `import('@/views/zone/index.vue')` 里那个 zone
             # 不算（它在 '/views/zone/index.vue' 字符串中间），
             # 只有 `path: 'zone'` 才算——注册了路由。
             ("web/src/router.ts", "zone", "literal"),
             # **侧边栏菜单项也要有**——用 **literal + 完整路径**：
             # 菜单写在 `BasicLayout.vue` 的 `menuGroups` 数组里
             # （**不是**从路由自动生成的），路径是 '/zone'（带斜杠）。
             #
             # **为什么这条不能省**（2026-10-29 实测撞出来的）：
             # 模型只注册了路由、没加菜单项 → 页面上侧边栏**找不到库区管理**，
             # 而判据是绿的——路由的 `meta.title` 里也有「库区管理」，
             # 把「有路由」误当成了「菜单里有」。
             # **产物层面分不出这两者**（路由表和菜单数组都编译进同一个主 chunk），
             # 所以这条只能在**源码层**判——归开发盒。
             ("web/src/layouts", "/zone", "literal"),
             # ---- 库位已用的库区要在库区管理里存在 ----
             #
             # **迁移里要有库区的种子数据**：
             # token 是 "INTO wms_zone"（不是 "wms_zone"）——
             # V5 那条建表迁移里也有 `wms_zone`（CREATE TABLE wms_zone），
             # 用 "wms_zone" 会被**建表迁移**满足 → 假绿：
             # 表建了、一条库区都没有，库位引用全是孤儿，
             # 库位页面编辑任何一条都报「库区不存在」。
             # "INTO wms_zone" 只在 INSERT 里出现。
             ("db/migration", "INTO wms_zone"),
             # **库位页面要从库区列表里选**（不许手填编码）：
             # 库位页面里真的用上了库区接口（ts_usage 排除只 import）。
             ("web/src/views/location", "zoneApi", "ts_usage")),

    # ---- 主数据与单据：实体 + Mapper + 对外接口 ----
    #
    # **为什么补这一组**（2026-10-30 实测撞出来的）：
    # 上一轮链跑里，需求提到「库位归属在库区上」→ 期望的概念是
    # zone / location / warehouse。zone 有实现证据（判「已实现」），
    # **location 和 warehouse 没有登记** → 状态是「判不了」→
    # 零改动的交付被判红，理由写着「实现状态判不了（没有登记实现证据）」。
    #
    # 「判不了」不等于「没实现」，可它**也不能算已实现**
    # （认不出就判红）。真正的解法是**把证据补上**——
    # 证据表是「这个概念在代码里长什么样」的登记表，
    # 仓里明明有这些主数据，登记表却漏了。
    #
    # **证据只登记「结构性」的**（实体 + Mapper + 对外接口）：
    # 这三样齐了 = 这个业务对象存在、能存、能被调。
    # 这是**结构**层面的事实，抄名字不抄机制——
    # 机制类概念（fifo/frozen/safety_stock/unique_slot/wave_merge…）
    # 的证据必须是**机制**（像 fefo 的「按 expire_date 排序」），
    # 只登记一个字段名 = 给模型开了一道「加个同名字段就算实现」的免检门，
    # 那比没有证据更坏（假实现）。所以机制类概念宁可留「判不了」。
    "sku": (("entity/Sku", "class Sku"),
            ("mapper/SkuMapper", "SkuMapper"),
            ("module/sku", "SkuController")),
    "inventory": (("entity/Inventory", "class Inventory"),
                  ("mapper/InventoryMapper", "InventoryMapper"),
                  ("module/inventory", "InventoryController")),
    "location": (("entity/Location", "class Location"),
                 ("mapper/LocationMapper", "LocationMapper"),
                 ("module/location", "LocationController")),
    "warehouse": (("entity/Warehouse", "class Warehouse"),
                  ("mapper/WarehouseMapper", "WarehouseMapper"),
                  ("module/warehouse", "WarehouseController")),
    "inbound": (("entity/InboundOrder", "class InboundOrder"),
                ("mapper/InboundOrderMapper", "InboundOrderMapper"),
                ("module/inbound", "InboundOrderController")),
    "outbound": (("entity/OutboundOrder", "class OutboundOrder"),
                 ("mapper/OutboundOrderMapper", "OutboundOrderMapper"),
                 ("module/outbound", "OutboundOrderController")),
    "wave": (("entity/Wave", "class Wave"),
             ("mapper/WaveMapper", "WaveMapper"),
             ("module/outbound", "WaveController")),
    # 盘点：整条单据链齐了才算盘点存在
    "count": (("entity/StocktakeOrder", "class StocktakeOrder"),
              ("mapper/StocktakeMapper", "StocktakeMapper"),
              ("module/stocktake", "StocktakeOrderController")),

    # ---- 约束：实现的机制 ----
    "fefo": (("mapper/InventoryMapper", "ORDER BY expire_date"),),
    "lot": (("mapper/InventoryMapper", "expire_date"),),

    # ---- 报表 ----
    "inventory_dashboard": (("vo/DashboardVO", "class DashboardVO"),),
}


def implementation_tokens(concept: str) -> tuple[str, ...]:
    """这个概念在源码里的实现证据（标识符片段；没有 = 判不了）"""
    return tuple(tok for _hint, tok, *_rest in implementation_evidence(concept))


def implementation_evidence_description(concept: str) -> str:
    """这个概念的证据长什么样（给人看的：哪个文件里的哪个标识符）

    「vo/DashboardVO 里的 pickAccuracy」——模型得知道**落在哪个文件**，
    否则它会在别处造一个同名的东西，机械层照样判不了它落地。
    """
    parts = [
        (f"{hint} 里的 {tok}" if hint else tok)
        for hint, tok, *_rest in implementation_evidence(concept)
    ]
    return "、".join(parts)


def implementation_evidence(concept: str) -> tuple[tuple[str, ...], ...]:
    """这个概念的证据（文件落点提示 + 标识符片段 [+ 模式]）

    **模式**：
    - 缺省（`any`）：标识符在那个文件里出现过就算数
    - `usage`：标识符必须出现在**真正用上它的地方**——
      只 import、只注入字段、只写在构造参数里**不算数**。

    **为什么要有 `usage`**（2026-10-28 实测）：
    库位服务为了满足「库位这一侧要引用库区」的证据，
    `import` 了 ZoneMapper、注入成字段，**一行都没用**——
    证据是绿的，可「库位挂到不存在的库区要被拒」这件事压根没实现。
    **一个没用到的 import 骗过了证据**，这类假实现比没有实现更坏。
    """
    return WMS_CONCEPT_IMPL_EVIDENCE.get(concept, ())


# ---- 证据的三种模式 ----
EVIDENCE_ANY = "any"
EVIDENCE_USAGE = "usage"
#: 前端版「真的用上了」：**排除 import 语句**之后还出现过。
#:
#: **为什么前端也要这个模式**：Java 那边的骗法是「import + 注入字段，
#: 一行业务逻辑都没写」；前端是「import { zoneApi } from '@/api/zone'，
#: 然后一次都没调用」。两边是**同一族病**——只提到、没用上。
#:
#: 前端没有 Java 那套「字段声明 / 参数列表 / 纯赋值」的骗法，
#: 所以规则可以简单：**import 之外还出现过，就算用上了**。
EVIDENCE_TS_USAGE = "ts_usage"
#: 被引号**单独**包住的字面量（'zone' / "zone"）。
#:
#: **为什么路由要用它**：`import('@/views/zone/index.vue')` 里也有 zone，
#: 但那是**文件路径**，不是**路由注册**。用 literal 模式，
#: 只有 `path: 'zone'` 才算数——证据说的是「注册了路由」，
#: 就不能让一个路径字符串冒充它。
EVIDENCE_LITERAL = "literal"


def _ts_usage_lines(text: str) -> list[str]:
    """前端文件里**排除 import 之后**的那些行

    排除三类：
    1. import 语句（**含跨行的**——`import {` 后面几行都不算）
    2. re-export（`export { x } from '...'`）
    3. `//` 行注释
    """
    out: list[str] = []
    in_import = False
    for line in text.splitlines():
        stripped = line.strip()
        if in_import:
            # import 语句结束于带 from 的那一行
            if "from" in stripped or stripped.endswith(";"):
                in_import = False
            continue
        if stripped.startswith("import"):
            # 一行写完的 import（`import x from 'y'`）当场就结束了
            in_import = "from" not in stripped
            continue
        if stripped.startswith("export") and " from " in stripped:
            continue
        if stripped.startswith("//"):
            continue
        out.append(line)
    return out


def _literal_found(text: str, token: str) -> bool:
    """token 有没有被引号**单独**包住（'token' / "token"）"""
    return re.search(r"['\"]" + re.escape(token) + r"['\"]", text) is not None


def _usage_lines(text: str) -> list[str]:
    """这个文件里**真正用上东西**的那些行

    排除四类「只是提到、不是用」的行：
    1. import / package
    2. 字段声明（`private final ZoneMapper zoneMapper;`）
    3. **方法/构造器的参数列表**（整段，含跨行的续行）
    4. 纯赋值（`this.zoneMapper = zoneMapper;`）

    **第 3 条是踩过的坑**：构造函数的参数列表常常跨行，
    续行 `ZoneMapper zoneMapper) {` 里也有那个类型名——
    只按「行首是访问修饰符」判断的话，它会被当成「用上了」。
    实测：一个只 import + 注入、**一行业务逻辑都没写**的服务，
    就这样骗过了证据。
    """
    out: list[str] = []
    depth = 0          # 当前的花括号深度
    in_params = False  # 正在跳过参数列表
    for line in text.splitlines():
        stripped = line.strip()
        # **深度每行都要更新**——被跳过的行也改深度
        # （类声明那行就被跳过了，不更新的话后面全错位）
        delta = line.count("{") - line.count("}")
        if in_params:
            if ")" in stripped:
                in_params = False
            depth += delta
            continue
        if not stripped or stripped.startswith(("import ", "package ")):
            depth += delta
            continue
        # **类体里的声明**（方法/构造器签名）：参数列表整段不算「用上」
        if depth == 1 and "(" in stripped and ";" not in stripped:
            if stripped.count("(") > stripped.count(")"):
                in_params = True
            depth += delta
            continue
        # 字段声明
        if re.match(r"(?:private|public|protected|static|final)\b", stripped) \
                and "(" not in stripped:
            depth += delta
            continue
        # 纯赋值：只是把注入进来的字段存下来
        if re.match(r"^[\w.]+\s*=\s*[\w]+\s*;$", stripped):
            depth += delta
            continue
        out.append(line)
        depth += delta
    return out


def _token_found(text: str, token: str, mode: str) -> bool:
    """标识符在这个文件里算不算「出现了」

    `usage` 模式：**只看真正用上它的地方**——
    import 行、字段声明、参数列表都不算（Java）。

    `ts_usage` 模式：排除 import 语句之后还出现过（前端）。

    `literal` 模式：被引号**单独**包住（'token'），
    避免路径字符串冒充路由/键名。
    """
    if mode == EVIDENCE_USAGE:
        lowered = token.lower()
        return any(lowered in line.lower() for line in _usage_lines(text))
    if mode == EVIDENCE_TS_USAGE:
        lowered = token.lower()
        return any(lowered in line.lower() for line in _ts_usage_lines(text))
    if mode == EVIDENCE_LITERAL:
        return _literal_found(text, token)
    return token in text


def missing_evidence(
    concept: str,
    files: Optional[dict[str, str]] = None,
    source_text: str = "",
) -> list[tuple[str, str]]:
    """这个概念**还差哪几处证据**（哪处没查到）

    **为什么单独列出来**：只说「zone 找不到」，模型无从下手——
    它不知道是实体没建、还是库位那侧没引用库区。
    报错要带能修它的材料（同一族：编译器的错误是最好的回喂材料）。
    """
    evidence = implementation_evidence(concept)
    if not evidence:
        return []
    if files is None:
        return [
            (hint, tok) for hint, tok, *_rest in evidence
            if not _token_found(source_text, tok, _mode_of(_rest))
        ]
    return [
        (hint, tok) for hint, tok, *_rest in evidence
        if not any(
            hint in path and _token_found(text, tok, _mode_of(_rest))
            for path, text in files.items()
        )
    ]


def _mode_of(rest: list[Any]) -> str:
    """证据三元组的第三位是模式（缺省 any）"""
    return str(rest[0]) if rest else EVIDENCE_ANY


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
        if any(hint for hint, _tok, *_rest in evidence):
            return "missing"
        return (
            "implemented"
            if all(
                _token_found(source_text, tok, _mode_of(rest))
                for _hint, tok, *rest in evidence
            )
            else "missing"
        )
    for hint, tok, *rest in evidence:
        mode = _mode_of(rest)
        if not any(
            hint in path and _token_found(text, tok, mode)
            for path, text in files.items()
        ):
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
