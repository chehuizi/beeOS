"""
boxes.wms_testing.probes - WMS 业务场景探针

**一条预期怎么才算被验证过**：靠**探针**——探针拿真的 WMS 实例跑一遍
业务场景，把实测值取回来，和预期比。

**三种结论，不是一种**：

| 结论 | 含义 | 算不算「验过」 |
|---|---|---|
| `pass` | 实测满足预期 | 算 |
| `fail` | 实测不满足预期（**带定位证据**） | 算 |
| `not_applicable` | **没有针对这个概念的探针** | 不算 |
| `indeterminate` | 探针跑了，但**判不了**（没数据 / 不许造数） | 不算 |

**为什么 n/a 和 indeterminate 必须分开**：
「没有探针」是**我们还没写**，「判不了」是**现场没条件**。
混成一个「跳过」，看板上就分不清「没做」和「做不了」——
而这两件事该做的事完全不同。

**探针住在这只盒子里，不在核心里**：WMS 的口径是业务知识。

**不降级**：探针跑不起来（连不上 WMS / 接口报错）→ 抛异常中止履约，
不是记一句「跳过」然后判绿。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Optional
from uuid import uuid4

from boxes.wms_testing.wms_client import WmsClient, WmsError, WmsHttpError


OUTCOME_PASS = "pass"
OUTCOME_FAIL = "fail"
OUTCOME_NOT_APPLICABLE = "not_applicable"
OUTCOME_INDETERMINATE = "indeterminate"

# 探针专用商品的编码前缀。
#
# **为什么要专用商品**：同一个 SKU 上如果还有别的库存，
# 分配可能挑中别的批次——那时候「挑错了」就归因不到这次场景上，
# 探针只能判「无法判定」。专用商品保证**这次场景的批次就是候选集**，
# 判定才有意义。
PROBE_SKU_PREFIX = "PROBE-FEFO-"

# 探针用的两个批次的效期：一个近（30 天窗口内）、一个远（窗口外）。
NEAR_EXPIRY_DAYS = 10
FAR_EXPIRY_DAYS = 300
BATCH_QTY = 100.0

# 「临期」的口径：30 天内到期（跟 WMS 看板的 expiringSoonSkuCount 一致）。
EXPIRING_WINDOW_DAYS = 30


@dataclass
class ProbeResult:
    """一条探针的结论"""

    outcome: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def determined(self) -> bool:
        """有没有得出确定结论（pass / fail）"""
        return self.outcome in (OUTCOME_PASS, OUTCOME_FAIL)


@dataclass
class ProbeContext:
    """探针跑起来要的东西"""

    client: WmsClient
    #: 实例模式。`verification` 才允许造数（写场景）。
    #: **生产实例上造单据 = 往真实业务里塞假数据**，不能默认允许。
    mode: str = "production"
    today: date = field(default_factory=date.today)
    requirements: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: **前端构建产物目录**（开发盒交付的那一份 dist）。
    #:
    #: **为什么探针要能看文件**：前端没有「调接口」这一步——
    #: 页面在浏览器里跑，测试盒够不着浏览器。
    #: 能机械判的是**产物里有没有那套页面**：
    #: 产物里有 `/zones`（页面真的调了库区接口）+
    #: 产物里有「库区管理」（菜单/路由标题真的注册了）。
    #:
    #: **为什么是「开发盒交付的那一份」**：跟后端一样——
    #: 测的必须是交付物本身，测别处的一份等于没测。
    frontend_dist_path: str = ""
    #: 这次到底有没有前端。**False 不等于「前端通过了」**——
    #: 那只是「这次压根没前端」，前端探针据此跳过（并说明）。
    frontend_expected: bool = False

    @property
    def may_mutate(self) -> bool:
        return self.mode == "verification"


@dataclass
class Probe:
    """一个概念的探针"""

    concept: str
    scenario_id: str
    check: Callable[[ProbeContext, dict[str, Any]], ProbeResult]


@dataclass
class Scenario:
    """一个业务场景（准备数据 + 一组探针）"""

    scenario_id: str
    #: 会不会写数据。写数据的场景只允许跑在验证实例上。
    mutates: bool
    prepare: Callable[[ProbeContext], dict[str, Any]]
    probes: list[Probe] = field(default_factory=list)


# ============================================================
# 场景：批次效期先出（FEFO）
# ============================================================


def _probe_sku(ctx: ProbeContext) -> dict[str, Any]:
    """备一个**探针专用商品**——保证候选集就是这次场景的批次

    **为什么必须是专用商品**：同一个 SKU 上如果还有别的库存，
    分配可能挑中别的批次——那时候「挑错了」就归因不到这次场景上，
    探针只能判「无法判定」。专用商品保证**这次场景的批次就是候选集**，
    判定才有意义。

    **为什么每次都建新的**：上一轮跑完，远效期那个批次还留在库里
    （出库只提走了近效期批次）。复用它 = 候选集里混着上一轮的东西。
    所以编码带随机后缀，每轮一个干净的商品。

    代价：验证实例的主数据里会多出探针商品（每个都标了
    `remark=beeOS 测试盒自动创建`）。这是**验证实例**才允许的代价——
    生产实例上根本不会跑到这一步（造数场景被挡住）。
    """
    client = ctx.client
    suffix = uuid4().hex[:6]
    created = client.create_sku({
        "code": f"{PROBE_SKU_PREFIX}{ctx.today.strftime('%Y%m%d')}-{suffix}",
        "name": "探针专用商品（批次效期探针）",
        "category": "探针",
        "unit": "箱",
        "batchManaged": 1,
        "shelfLifeDays": 365,
        "remark": "beeOS 测试盒自动创建，勿用于真实业务",
    })
    return created


def _pick_locations(ctx: ProbeContext) -> tuple[int, int, int]:
    """找一个仓 + 它的收货位（type2）+ 存储位（type1）

    返回 (warehouse_id, receiving_location_id, storage_location_id)
    """
    client = ctx.client
    locations = client.list_locations()
    by_warehouse: dict[int, list[dict[str, Any]]] = {}
    for loc in locations:
        by_warehouse.setdefault(loc.get("warehouseId"), []).append(loc)

    for warehouse_id, locs in sorted(by_warehouse.items(), key=lambda kv: kv[0] or 0):
        receiving = [l for l in locs if l.get("locationType") == 2]
        storing = [l for l in locs if l.get("locationType") == 1]
        if receiving and storing:
            # 容量最大的存储位（一次要放两个批次）
            storing.sort(key=lambda l: l.get("capacity") or 0, reverse=True)
            return warehouse_id, receiving[0]["id"], storing[0]["id"]
    raise WmsError("WMS 里找不到可用的仓（要同时有 type2 收货位和 type1 存储位）")


def _receive_and_putaway(
    ctx: ProbeContext,
    *,
    warehouse_id: int,
    receiving_location_id: int,
    storage_location_id: int,
    sku_id: int,
    batch_no: str,
    produced_date: str,
    expire_date: str,
    qty: float,
) -> dict[str, Any]:
    """造一个批次：建单 → 提交 → 收货（带效期）→ 上架

    **上架这一步是关键**：WMS 就是在这一步把批次的效期弄丢的
    （`wms_putaway_task` 没有 expire_date 两列）。
    所以探针必须走完整条路，不能只收货就下结论——
    只收货的话效期还在，测的就不是真实场景。
    """
    client = ctx.client
    order = client.create_inbound_order({
        "warehouseId": warehouse_id,
        "supplierCode": "SUP-BEEOS-PROBE",
        "supplierName": "beeOS 测试盒",
        "remark": "beeOS 测试盒探针造数",
        "items": [{"skuId": sku_id, "expectedQty": qty}],
    })
    inbound_id = order["id"]
    client.submit_inbound_order(inbound_id)
    detail = client.get_inbound_order(inbound_id)
    items = detail.get("items") or []
    if not items:
        raise WmsError(f"入库单 {inbound_id} 没有明细行，收货无从下手")
    receipt = client.receive_inbound_order(inbound_id, {
        "locationId": receiving_location_id,
        "items": [{
            "inboundItemId": items[0]["id"],
            "batchNo": batch_no,
            "producedDate": produced_date,
            "expireDate": expire_date,
            "qty": qty,
        }],
    })
    tasks = client.list_putaway_tasks(inbound_id)
    if not tasks:
        raise WmsError(f"入库单 {inbound_id} 收货后没有生成上架任务")
    client.execute_putaway(tasks[0]["id"], {
        "toLocationId": storage_location_id,
        "operator": "beeOS 测试盒",
    })
    receipt_items = receipt.get("items") or []
    return {
        "batch_no": batch_no,
        "expire_date": expire_date,
        "produced_date": produced_date,
        "qty": qty,
        "inbound_id": inbound_id,
        # 收货单上记的效期——**这是业务事实**。
        # 库存行上的效期是上架之后的样子，可能是空的。
        "receipt_expire_date": (receipt_items[0].get("expireDate") if receipt_items else None),
    }


def _prepare_batch_expiry(ctx: ProbeContext) -> dict[str, Any]:
    """场景准备：两个批次（近效期 / 远效期）+ 一次出库分配"""
    client = ctx.client
    sku = _probe_sku(ctx)
    warehouse_id, receiving_id, storage_id = _pick_locations(ctx)

    customers = client.list_customers()
    if not customers:
        raise WmsError("WMS 里没有客户，出库单建不了")
    customer = customers[0]

    stamp = f"{ctx.today.strftime('%Y%m%d')}-{uuid4().hex[:6]}"
    near_expire = (ctx.today + timedelta(days=NEAR_EXPIRY_DAYS)).isoformat()
    far_expire = (ctx.today + timedelta(days=FAR_EXPIRY_DAYS)).isoformat()
    produced = (ctx.today - timedelta(days=30)).isoformat()

    batches = [
        _receive_and_putaway(
            ctx,
            warehouse_id=warehouse_id,
            receiving_location_id=receiving_id,
            storage_location_id=storage_id,
            sku_id=sku["id"],
            batch_no=f"B-NEAR-{stamp}",
            produced_date=produced,
            expire_date=near_expire,
            qty=BATCH_QTY,
        ),
        _receive_and_putaway(
            ctx,
            warehouse_id=warehouse_id,
            receiving_location_id=receiving_id,
            storage_location_id=storage_id,
            sku_id=sku["id"],
            batch_no=f"B-FAR-{stamp}",
            produced_date=produced,
            expire_date=far_expire,
            qty=BATCH_QTY,
        ),
    ]

    outbound = client.create_outbound_order({
        "warehouseId": warehouse_id,
        "customerId": customer["id"],
        "priority": 5,
        "remark": "beeOS 测试盒探针造数",
        "items": [{"skuId": sku["id"], "orderedQty": BATCH_QTY}],
    })
    outbound_id = outbound["id"]
    client.submit_outbound_order(outbound_id)
    client.allocate_outbound_order(outbound_id)
    detail = client.get_outbound_order(outbound_id)
    allocations = detail.get("allocations") or []
    allocated = allocations[0] if allocations else None

    inventory_rows = [
        row for row in client.list_inventories()
        if row.get("skuId") == sku["id"]
    ]

    return {
        "sku": sku,
        "warehouse_id": warehouse_id,
        "receiving_location_id": receiving_id,
        "storage_location_id": storage_id,
        "batches": batches,
        "outbound_id": outbound_id,
        "allocated": {
            "batch_no": allocated.get("batchNo") if allocated else None,
            "qty": allocated.get("allocatedQty") if allocated else None,
            "location_code": allocated.get("locationCode") if allocated else None,
        },
        "inventory_rows": inventory_rows,
        "dashboard": client.dashboard(),
        "today": ctx.today.isoformat(),
        "expiring_window_days": EXPIRING_WINDOW_DAYS,
    }


# ---- 探针本体 ----


def _stored_expiry_by_batch(facts: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """库存行按批次分组（只取在库的那些）

    在库 = on_hand_qty > 0。收货位上那些 qty=0 的行不算——
    它们是收货时的残留记录，效期还在，跟上架后的库存不是一回事。
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in facts.get("inventory_rows") or []:
        if (row.get("onHandQty") or 0) <= 0:
            continue
        grouped.setdefault(row.get("batchNo") or "", []).append(row)
    return grouped


def _lost_expiry_batches(facts: dict[str, Any]) -> list[dict[str, Any]]:
    """哪些批次**上架之后效期丢了**

    对比两个事实：
    - 收货时记下的效期（业务事实，WMS 当时确实记了）
    - 上架后在库库存行上的效期（系统实际用于排序的那个）

    不一致 = 效期在路上丢了。
    """
    grouped = _stored_expiry_by_batch(facts)
    lost: list[dict[str, Any]] = []
    for batch in facts.get("batches") or []:
        received = batch.get("expire_date")
        rows = grouped.get(batch.get("batch_no") or "", [])
        stored = [r.get("expireDate") for r in rows]
        if not rows:
            continue
        if any(value != received for value in stored):
            lost.append({
                "batch_no": batch.get("batch_no"),
                "received_expire_date": received,
                "stored_expire_date": stored,
                "location_codes": [r.get("locationCode") for r in rows],
            })
    return lost


def _check_lot_expiry(ctx: ProbeContext, facts: dict[str, Any]) -> ProbeResult:
    """批次效期要**随货移动**（收货记了，上架不能丢）

    这是 FEFO 的**数据前提**：效期丢了，分配就无从按效期排序。
    所以它单独一条探针——「批次」这个概念讲的就是这件事
    （合同 `API_CONTRACT_BIZ.md` §15.2「批次和序列号随货移动」）。
    """
    lost = _lost_expiry_batches(facts)
    if not lost:
        stored = _stored_expiry_by_batch(facts)
        return ProbeResult(
            OUTCOME_PASS,
            "上架之后库存行仍带着批次的效期",
            {"stored_batches": sorted(stored.keys())},
        )
    return ProbeResult(
        OUTCOME_FAIL,
        f"{len(lost)} 个批次上架后效期丢了（收货时记了，上架后没了）",
        {
            "stored_inventory": [
                {
                    "batch_no": row.get("batchNo"),
                    "expire_date": row.get("expireDate"),
                    "produced_date": row.get("producedDate"),
                    "location_code": row.get("locationCode"),
                    "on_hand_qty": row.get("onHandQty"),
                }
                for row in facts.get("inventory_rows") or []
            ],
            "expiring_window_days": EXPIRING_WINDOW_DAYS,
        },
    )


def _check_fefo(ctx: ProbeContext, facts: dict[str, Any]) -> ProbeResult:
    """效期先出：出库分配必须挑**效期最早**的那一批

    **判据有两层**：

    1. **前提**：候选批次的效期还在系统里。
       不在的话，分配排序键全是空——**挑中哪个批次是巧合，不是效期先出**。
       （实测：WMS 上架丢效期 → 所有候选的 expire_date 都是 null →
       排序退化成「随便挑」，而我们那个近效期批次恰好先入库，
       于是「碰巧挑对了」，看起来像 FEFO 生效了。）
    2. **结果**：分配的批次确实是效期最早的那个。

    前提不成立就**判红**——「碰巧对」不是「满足需求」。
    """
    batches = facts.get("batches") or []
    allocated = facts.get("allocated") or {}
    if not batches:
        return ProbeResult(OUTCOME_INDETERMINATE, "场景没有造出批次，无从判定")
    if not allocated.get("batch_no"):
        return ProbeResult(
            OUTCOME_INDETERMINATE,
            "出库没有分配到任何批次",
            {"outbound_id": facts.get("outbound_id")},
        )

    earliest = min(batches, key=lambda b: b.get("expire_date") or "")
    allocated_batch = next(
        (b for b in batches if b.get("batch_no") == allocated.get("batch_no")),
        None,
    )
    candidates = [
        {
            "batch_no": b.get("batch_no"),
            "expire_date": b.get("expire_date"),
            "qty": b.get("qty"),
        }
        for b in batches
    ]
    # 库存行上**实际存着的**效期（跟收货时记的不一样就是丢了）
    stored = [
        {
            "batch_no": row.get("batchNo"),
            "expire_date": row.get("expireDate"),
            "produced_date": row.get("producedDate"),
            "location_code": row.get("locationCode"),
            "on_hand_qty": row.get("onHandQty"),
        }
        for row in facts.get("inventory_rows") or []
    ]

    picked_right = allocated.get("batch_no") == earliest.get("batch_no")

    # ---- 第 1 层：前提 ----
    lost = _lost_expiry_batches(facts)
    if lost:
        return ProbeResult(
            OUTCOME_FAIL,
            f"批次效期在上架时丢了，分配无从按效期排序——"
            f"这次挑中 {allocated.get('batch_no')}（应为 {earliest.get('batch_no')}）"
            f"{'只是巧合' if picked_right else '而且挑错了'}",
            {
                "allocated": allocated.get("batch_no"),
                "allocated_expire_date": (
                    allocated_batch.get("expire_date") if allocated_batch else None
                ),
                "expected_batch_no": earliest.get("batch_no"),
                "expected_expire_date": earliest.get("expire_date"),
                "candidates": candidates,
                "stored_inventory": stored,
                "outbound_id": facts.get("outbound_id"),
                "near_batch_still_has_expiry": not lost,
            },
        )

    # ---- 第 2 层：结果 ----
    if picked_right:
        return ProbeResult(
            OUTCOME_PASS,
            f"分配到了效期最早的批次 {earliest.get('batch_no')}"
            f"（效期 {earliest.get('expire_date')}）",
            {"allocated": allocated.get("batch_no"), "candidates": candidates},
        )

    return ProbeResult(
        OUTCOME_FAIL,
        f"分配到了批次 {allocated.get('batch_no')}，"
        f"但效期最早的是 {earliest.get('batch_no')}"
        f"（效期 {earliest.get('expire_date')}）",
        {
            "allocated": allocated.get("batch_no"),
            "allocated_expire_date": (
                allocated_batch.get("expire_date") if allocated_batch else None
            ),
            "expected_batch_no": earliest.get("batch_no"),
            "expected_expire_date": earliest.get("expire_date"),
            "candidates": candidates,
            "stored_inventory": stored,
            "outbound_id": facts.get("outbound_id"),
        },
    )


def _check_expiring_sku_count(ctx: ProbeContext, facts: dict[str, Any]) -> ProbeResult:
    """临期 SKU 数：放了近效期批次在库，看板报的临期 SKU 数必须 ≥ 1

    **判据用业务事实，不用系统自己的数**：
    场景明确放了一个 30 天内到期的批次在库——**这是真的**。
    看板报 0，就是「临期的 SKU 看板上看不到」。
    """
    dashboard = facts.get("dashboard") or {}
    if "expiringSoonSkuCount" not in dashboard:
        return ProbeResult(
            OUTCOME_FAIL,
            "库存看板没有报临期 SKU 数（expiringSoonSkuCount 字段不存在）",
            {"dashboard_keys": sorted(dashboard.keys())},
        )
    count = dashboard.get("expiringSoonSkuCount")
    window_end = _plus_days(facts, EXPIRING_WINDOW_DAYS)
    near = next(
        (b for b in facts.get("batches") or []
         if b.get("expire_date") and window_end and b["expire_date"] <= window_end),
        None,
    )
    stored_near = [
        row for row in facts.get("inventory_rows") or []
        if (row.get("expireDate") or "") and row.get("batchNo") == (near or {}).get("batch_no")
    ]
    if count and count >= 1:
        return ProbeResult(
            OUTCOME_PASS,
            f"看板报临期 SKU 数 {count}（场景放了 1 个窗口内到期的批次）",
            {"expiringSoonSkuCount": count},
        )
    return ProbeResult(
        OUTCOME_FAIL,
        f"库里放着 {near.get('batch_no') if near else '近效期批次'}"
        f"（效期 {near.get('expire_date') if near else '?'}），"
        f"看板报的临期 SKU 数却是 {count}",
        {
            "expiringSoonSkuCount": count,
            "near_batch": (near or {}).get("batch_no"),
            # 库存行上的效期是空的 → 看板当然数不到
            "stored_inventory": [
                {
                    "batch_no": row.get("batchNo"),
                    "expire_date": row.get("expireDate"),
                    "produced_date": row.get("producedDate"),
                    "location_code": row.get("locationCode"),
                    "on_hand_qty": row.get("onHandQty"),
                }
                for row in facts.get("inventory_rows") or []
            ],
            "near_batch_still_has_expiry": bool(stored_near),
            "expiring_window_days": EXPIRING_WINDOW_DAYS,
        },
    )


def _plus_days(facts: dict[str, Any], days: int) -> str:
    today = facts.get("today") or ""
    try:
        return (date.fromisoformat(today) + timedelta(days=days)).isoformat()
    except ValueError:
        return ""


def _check_inventory_dashboard(ctx: ProbeContext, facts: dict[str, Any]) -> ProbeResult:
    """库存看板：那个**面**得在（业务方说「要在看板看到」）

    这里验的是「面有没有」——看板端点得真的报得出临期这一项。
    「数对不对」由 `wms_expiring_soon_sku_count` 那条探针管。
    """
    dashboard = facts.get("dashboard") or {}
    if "expiringSoonSkuCount" not in dashboard:
        return ProbeResult(
            OUTCOME_FAIL,
            "库存看板没有临期这一项——业务方要的「在看板看到」无从谈起",
            {"dashboard_keys": sorted(dashboard.keys())},
        )
    return ProbeResult(
        OUTCOME_PASS,
        "库存看板报了临期 SKU 数",
        {
            "expiringSoonSkuCount": dashboard.get("expiringSoonSkuCount"),
            "dashboard_keys": sorted(dashboard.keys()),
        },
    )


# ============================================================
# 场景：库区管理（仓库下的分区；库位挂在它上面）
# ============================================================

# 探针用的库区/库位编码前缀——跟批次一样用专用前缀，
# 免得跟仓里已有的主数据（真实库位）撞上。
ZONE_CODE_PREFIX = "PROBE-ZONE-"
LOCATION_CODE_PREFIX = "PROBE-LOC-"
# 故意指向一个**不存在的库区**——用来验「库位不能挂在不存在的库区上」
BOGUS_ZONE_CODE = "PROBE-ZONE-NOT-EXIST"


def _prepare_zone(ctx: ProbeContext) -> dict[str, Any]:
    """在验证实例上真跑一遍库区管理

    跑的五件事（对应需求里的五句话）：
    1. 新增库区
    2. 查得到（列表里有它）
    3. 改得了（改名后读出来是新名字）
    4. **库位挂在这个库区上**（存库位时校验库区存在）
    5. **库区下还有库位时不允许删除**

    **接口还没有的时候不许抛异常**：那是「现场没条件」，
    不是「业务没做对」——两者混成一个结论，报告就没法看了。
    """
    facts: dict[str, Any] = {"zone_api_missing": False}

    # ---- 先看接口在不在 ----
    try:
        ctx.client.list_zones()
    except WmsHttpError as exc:
        if exc.status == 404:
            facts["zone_api_missing"] = True
            facts["detail"] = str(exc)
            return facts
        raise

    warehouses = ctx.client.list_warehouses()
    if not warehouses:
        facts["no_warehouse"] = True
        return facts
    # **必须挑一个「启用」的仓库**：
    # 仓里的库位/库区服务都要求仓库是启用的（停用的仓库连库位都建不了）。
    # 探针要是挑了停用的那个，报错会变成
    # 「仓库已停用」——那是**探针挑错了数据**，不是功能没实现，
    # 而它会让真正的结论被淹没（实测：库位挂不上 → 后面「库区能删」也跟着假失败）。
    active = [
        w for w in warehouses
        if w.get("status") in (1, "1", True)
    ]
    facts["warehouse_pool"] = [
        {"id": w.get("id"), "code": w.get("code"), "status": w.get("status")}
        for w in warehouses
    ]
    if not active:
        facts["no_active_warehouse"] = True
        return facts
    warehouse_id = active[0].get("id")
    facts["warehouse_code"] = active[0].get("code")

    suffix = uuid4().hex[:6].upper()
    zone_code = f"{ZONE_CODE_PREFIX}{suffix}"
    zone_name = f"探针库区-{suffix}"
    facts["warehouse_id"] = warehouse_id
    facts["zone_code"] = zone_code

    # ---- 1. 新增 ----
    try:
        zone = ctx.client.create_zone({
            "warehouseId": warehouse_id,
            "code": zone_code,
            "name": zone_name,
        })
    except WmsError as exc:
        facts["create_failed"] = str(exc)
        return facts
    facts["zone"] = zone
    zone_id = zone.get("id") if isinstance(zone, dict) else None
    facts["zone_id"] = zone_id
    if not zone_id:
        facts["create_failed"] = f"新增库区没返回 id：{zone!r}"
        return facts

    # ---- 2. 查得到 ----
    try:
        zones = ctx.client.list_zones()
    except WmsError as exc:
        facts["list_failed"] = str(exc)
        zones = []
    facts["listed_codes"] = sorted(
        str(z.get("code")) for z in zones if isinstance(z, dict)
    )
    facts["found_in_list"] = zone_code in facts["listed_codes"]

    # ---- 3. 改得了 ----
    new_name = f"探针库区-改-{suffix}"
    try:
        updated = ctx.client.update_zone(zone_id, {
            "warehouseId": warehouse_id,
            "code": zone_code,
            "name": new_name,
        })
        facts["updated"] = updated
        facts["update_ok"] = True
    except WmsError as exc:
        facts["update_ok"] = False
        facts["update_error"] = str(exc)
        new_name = zone_name
    try:
        reread = ctx.client.get_zone(zone_id)
        facts["reread_name"] = (
            reread.get("name") if isinstance(reread, dict) else None
        )
    except WmsError as exc:
        facts["reread_name"] = None
        facts["reread_error"] = str(exc)

    # ---- 4. 库位挂在库区上 ----
    loc_code = f"{LOCATION_CODE_PREFIX}{suffix}"
    facts["location_code"] = loc_code
    try:
        location = ctx.client.create_location({
            "warehouseId": warehouse_id,
            "code": loc_code,
            "zone": zone_code,
            "locationType": 1,
        })
        facts["location"] = location
        facts["location_id"] = (
            location.get("id") if isinstance(location, dict) else None
        )
        facts["location_created"] = True
    except WmsError as exc:
        facts["location_created"] = False
        facts["location_error"] = str(exc)
        facts["location_id"] = None

    # ---- 4b. 挂在不存在的库区上要被拒 ----
    try:
        bogus = ctx.client.create_location({
            "warehouseId": warehouse_id,
            "code": f"{LOCATION_CODE_PREFIX}B{suffix}",
            "zone": BOGUS_ZONE_CODE,
            "locationType": 1,
        })
        facts["bogus_zone_rejected"] = False
        facts["bogus_zone_accepted"] = bogus
        # 建出来了就顺手删掉，别留垃圾
        if isinstance(bogus, dict) and bogus.get("id"):
            try:
                ctx.client.delete_location(bogus["id"])
            except WmsError:
                pass
    except WmsError as exc:
        facts["bogus_zone_rejected"] = True
        facts["bogus_zone_error"] = str(exc)

    # ---- 5. 库区下还有库位时不允许删 ----
    try:
        ctx.client.delete_zone(zone_id)
        facts["delete_with_location_rejected"] = False
        facts["delete_with_location_succeeded"] = True
        zone_deleted = True
    except WmsError as exc:
        facts["delete_with_location_rejected"] = True
        facts["delete_with_location_error"] = str(exc)
        zone_deleted = False

    # ---- 收尾：把探针造的数据清掉 ----
    if facts.get("location_id"):
        try:
            ctx.client.delete_location(facts["location_id"])
            facts["location_cleaned"] = True
        except WmsError as exc:
            facts["location_cleaned"] = False
            facts["location_clean_error"] = str(exc)
    if not zone_deleted:
        try:
            ctx.client.delete_zone(zone_id)
            facts["zone_cleaned"] = True
        except WmsError as exc:
            facts["zone_cleaned"] = False
            facts["zone_clean_error"] = str(exc)
    else:
        facts["zone_cleaned"] = True
    return facts


def _check_zone_management(ctx: ProbeContext, facts: dict[str, Any]) -> ProbeResult:
    """库区管理：能增删改查，且**库位真的挂在库区上**

    判据是需求里那几句话逐条对上：
    - 「能新增」→ 建得出来
    - 「能查询」→ 列表里有它
    - 「能编辑」→ 改完读出来是新值
    - 「库位归属在库区上」→ 库位能挂上去；**挂到不存在的库区要被拒**
    - 「能删除」→ 库区下没库位时删得掉；**有库位时删不掉**
    """
    if facts.get("zone_api_missing"):
        return ProbeResult(
            OUTCOME_INDETERMINATE,
            "验证实例上还没有库区接口（404）——这次验不了，"
            "不能拿「没验到」当「做到了」",
            {"detail": facts.get("detail", "")},
        )
    if facts.get("no_warehouse"):
        return ProbeResult(
            OUTCOME_INDETERMINATE,
            "验证实例上没有仓库——库区挂在仓库下面，造不出场景",
            {},
        )
    if facts.get("no_active_warehouse"):
        return ProbeResult(
            OUTCOME_INDETERMINATE,
            "验证实例上没有**启用**的仓库——库位和库区都要挂在启用的仓库上，"
            "造不出场景",
            {"warehouses": facts.get("warehouse_pool") or []},
        )
    if facts.get("create_failed"):
        return ProbeResult(
            OUTCOME_FAIL,
            f"新增库区失败：{facts.get('create_failed')}",
            {"zone_code": facts.get("zone_code", "")},
        )

    problems: list[str] = []
    if not facts.get("found_in_list"):
        problems.append(
            f"新增的库区 {facts.get('zone_code')} 在列表里查不到"
        )
    if not facts.get("update_ok"):
        problems.append(f"编辑库区失败：{facts.get('update_error')}")
    else:
        updated = facts.get("updated")
        expected_name = updated.get("name") if isinstance(updated, dict) else None
        if expected_name is not None and facts.get("reread_name") != expected_name:
            problems.append(
                f"编辑后读回来的名字是 {facts.get('reread_name')!r}，"
                f"跟改成的 {expected_name!r} 不一样"
            )
    if not facts.get("location_created"):
        problems.append(
            f"库位挂不到库区上：{facts.get('location_error')}"
        )
    if facts.get("bogus_zone_rejected") is False:
        problems.append(
            f"库位挂到不存在的库区（{BOGUS_ZONE_CODE}）居然建成了——"
            "「库位归属在库区上」没有真的被校验"
        )
    if facts.get("delete_with_location_rejected") is False:
        problems.append(
            "库区下还有库位时居然删得掉——删掉就留下悬空的库位了"
        )

    details = {
        "zone_code": facts.get("zone_code", ""),
        "zone_id": facts.get("zone_id"),
        "found_in_list": facts.get("found_in_list"),
        "reread_name": facts.get("reread_name"),
        "location_code": facts.get("location_code", ""),
        "location_created": facts.get("location_created"),
        "bogus_zone_rejected": facts.get("bogus_zone_rejected"),
        "delete_with_location_rejected": facts.get(
            "delete_with_location_rejected"),
        "cleanup": {
            "location_cleaned": facts.get("location_cleaned"),
            "zone_cleaned": facts.get("zone_cleaned"),
        },
    }
    if problems:
        return ProbeResult(
            OUTCOME_FAIL,
            "库区管理有 " + str(len(problems)) + " 处没对上：" + "；".join(problems),
            details,
        )
    return ProbeResult(
        OUTCOME_PASS,
        "库区能增删改查，库位挂上了库区，不存在的库区被拒，"
        "库区下有库位时删不掉",
        details,
    )


def _check_zone_under_warehouse(ctx: ProbeContext, facts: dict[str, Any]) -> ProbeResult:
    """库区挂在**某个仓库**下面（库区不是悬空的）

    需求第一句就是「库区是仓库下的分区」——
    这条要验的是**归属关系**：库区得真的属于一个仓库。
    """
    if facts.get("zone_api_missing"):
        return ProbeResult(
            OUTCOME_INDETERMINATE,
            "验证实例上还没有库区接口（404）——这次验不了",
            {"detail": facts.get("detail", "")},
        )
    zone = facts.get("zone") or {}
    if not isinstance(zone, dict) or not zone.get("id"):
        return ProbeResult(
            OUTCOME_FAIL,
            f"没拿到库区：{facts.get('create_failed')}",
            {},
        )
    zone_warehouse = zone.get("warehouseId")
    if zone_warehouse is None:
        return ProbeResult(
            OUTCOME_FAIL,
            "库区没有 warehouseId——它不挂在任何仓库下面",
            {"zone": zone},
        )
    if zone_warehouse != facts.get("warehouse_id"):
        return ProbeResult(
            OUTCOME_FAIL,
            f"库区挂在仓库 {zone_warehouse} 下，"
            f"不是这次指定的仓库 {facts.get('warehouse_id')}",
            {"zone": zone},
        )
    return ProbeResult(
        OUTCOME_PASS,
        f"库区 {facts.get('zone_code')} 挂在仓库 {zone_warehouse} 下面",
        {"zone_code": facts.get("zone_code"), "warehouse_id": zone_warehouse},
    )


def _check_location_ownership(ctx: ProbeContext, facts: dict[str, Any]) -> ProbeResult:
    """库位归属在库区上：挂得上，且**挂到不存在的库区要被拒**

    「库位归属在库区上」不是一句说明，是**要被校验的**：
    - 挂到真库区 → 成功
    - 挂到不存在的库区 → **必须被拒**
      （不拒的话，「归属」就只是一段随便写的字符串）
    """
    if facts.get("zone_api_missing"):
        return ProbeResult(
            OUTCOME_INDETERMINATE,
            "验证实例上还没有库区接口（404）——这次验不了",
            {"detail": facts.get("detail", "")},
        )
    problems: list[str] = []
    if not facts.get("location_created"):
        problems.append(f"库位挂不到库区上：{facts.get('location_error')}")
    if facts.get("bogus_zone_rejected") is False:
        problems.append(
            f"库位挂到不存在的库区（{BOGUS_ZONE_CODE}）居然建成了——"
            "「库位归属在库区上」没有真的被校验"
        )
    details = {
        "zone_code": facts.get("zone_code", ""),
        "location_code": facts.get("location_code", ""),
        "location_created": facts.get("location_created"),
        "bogus_zone_rejected": facts.get("bogus_zone_rejected"),
    }
    if problems:
        return ProbeResult(
            OUTCOME_FAIL,
            "；".join(problems),
            details,
        )
    return ProbeResult(
        OUTCOME_PASS,
        f"库位 {facts.get('location_code')} 挂在库区 {facts.get('zone_code')} 上，"
        "挂到不存在的库区被拒",
        details,
    )


# ============================================================
# 前端探针：库区管理**页面**在不在
# ============================================================
#
# **为什么前端要单独一条**：后端接口做完了，业务方在页面上还是找不到
# 「库区管理」——那不叫交付完成。
#
# **怎么机械判**：前端没有「调接口」这一步（页面在浏览器里跑，
# 测试盒够不着浏览器），能判的是**构建产物里有没有那套页面**：
# - 产物里有 `/zones` → 页面真的调了库区接口
# - 产物里有「库区管理」或 'zone' 路由 → 页面真的注册进菜单/路由
#
# **判的是「开发盒交付的那一份 dist」**，不是别处随便一份——
# 跟后端「部署 jar 到验证实例再探」是同一个道理。

#: 产物里必须出现的**接口调用**痕迹（页面调了库区接口）
ZONE_FRONTEND_API_MARK = "/zones"
#: 产物里必须出现的**菜单标题**（设计盒契约里写死的那个）
ZONE_FRONTEND_TITLE = "库区管理"
#: 产物里必须出现的**路由**（注册了页面入口）
ZONE_FRONTEND_ROUTE = re.compile(r"""['"]/?zone['"]""")


def _frontend_dist_text(dist_dir: str) -> tuple[str, int]:
    """把前端产物读成一坨文本（顺便报文件数）

    **读什么**：.js / .html（vite 的产物就这两种）。
    **不读图片字体**：那是二进制，读出来也没信息。
    """
    if not dist_dir:
        return "", 0
    root = Path(dist_dir)
    if not root.exists():
        return "", 0
    chunks: list[str] = []
    count = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in (".js", ".html"):
            continue
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        count += 1
    return "\n".join(chunks), count


def _check_zone_frontend(ctx: ProbeContext, facts: dict[str, Any]) -> ProbeResult:
    """库区管理**页面**：前端产物里真的有那套页面

    **三种结论**：
    - 这次压根没前端落点 → `indeterminate`（**不是「前端通过了」**）
    - 产物里缺页面/缺菜单 → `fail`（带定位证据）
    - 两样都在 → `pass`
    """
    if not ctx.frontend_expected:
        return ProbeResult(
            OUTCOME_INDETERMINATE,
            "这次方案没有前端落点——前端没验"
            "（这不等于「前端通过了」，是压根没前端可验）",
            {"frontend_expected": False},
        )

    text, file_count = _frontend_dist_text(ctx.frontend_dist_path)
    if not ctx.frontend_dist_path:
        return ProbeResult(
            OUTCOME_INDETERMINATE,
            "没有前端构建产物的路径——这次验不了前端",
            {"frontend_expected": True, "frontend_dist_path": ""},
        )
    if file_count == 0:
        return ProbeResult(
            OUTCOME_INDETERMINATE,
            f"前端产物目录里没有 .js/.html：{ctx.frontend_dist_path}"
            "——这次验不了前端",
            {"frontend_expected": True, "frontend_dist_path": ctx.frontend_dist_path},
        )

    has_api = ZONE_FRONTEND_API_MARK in text
    has_title = ZONE_FRONTEND_TITLE in text
    has_route = ZONE_FRONTEND_ROUTE.search(text) is not None

    details = {
        "frontend_dist_path": ctx.frontend_dist_path,
        "artifact_file_count": file_count,
        "has_zone_api_call": has_api,
        "has_zone_menu_title": has_title,
        "has_zone_route": has_route,
    }

    missing: list[str] = []
    if not has_api:
        missing.append(
            f"产物里找不到「{ZONE_FRONTEND_API_MARK}」"
            "——页面没有真的调库区接口（只有页面壳子？）"
        )
    if not has_title and not has_route:
        missing.append(
            f"产物里找不到菜单标题「{ZONE_FRONTEND_TITLE}」，"
            "也没有 'zone' 路由——页面没有注册进菜单/路由"
            "（业务方在界面上找不到它）"
        )
    if missing:
        return ProbeResult(
            OUTCOME_FAIL,
            "前端产物里没有库区管理页面：" + "；".join(missing),
            details,
        )

    return ProbeResult(
        OUTCOME_PASS,
        "前端产物里有库区管理页面"
        f"（{file_count} 个产物文件，页面调了 {ZONE_FRONTEND_API_MARK}，"
        f"菜单/路由{'标题在' if has_title else '路由在'}）",
        details,
    )


ZONE_MANAGEMENT_SCENARIO = Scenario(
    scenario_id="zone_management",
    mutates=True,
    prepare=_prepare_zone,
    probes=[
        Probe("zone", "zone_management", _check_zone_management),
        Probe("warehouse", "zone_management", _check_zone_under_warehouse),
        Probe("location", "zone_management", _check_location_ownership),
        # **前端那一条**：后端做完了、页面上没有 = 没交付完
        Probe("zone", "zone_management", _check_zone_frontend),
    ],
)


BATCH_EXPIRY_SCENARIO = Scenario(
    scenario_id="batch_expiry_allocation",
    mutates=True,
    prepare=_prepare_batch_expiry,
    probes=[
        Probe("fefo", "batch_expiry_allocation", _check_fefo),
        Probe("lot", "batch_expiry_allocation", _check_lot_expiry),
        Probe("wms_expiring_soon_sku_count", "batch_expiry_allocation",
              _check_expiring_sku_count),
        Probe("inventory_dashboard", "batch_expiry_allocation",
              _check_inventory_dashboard),
    ],
)


SCENARIOS: dict[str, Scenario] = {
    BATCH_EXPIRY_SCENARIO.scenario_id: BATCH_EXPIRY_SCENARIO,
    ZONE_MANAGEMENT_SCENARIO.scenario_id: ZONE_MANAGEMENT_SCENARIO,
}


def find_probe(concept: str) -> Optional[tuple[Scenario, Probe]]:
    """这个概念有没有探针——**没有就明说没有**

    「没有探针」是**我们还没写**，不是「这条需求不重要」。
    混进「通过」里就是假绿。
    """
    for scenario in SCENARIOS.values():
        for probe in scenario.probes:
            if probe.concept == concept:
                return scenario, probe
    return None
