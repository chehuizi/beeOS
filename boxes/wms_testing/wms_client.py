"""
boxes.wms_testing.wms_client - WMS 的 HTTP 客户端

**这份知识住在盒子里，不在核心里**：WMS 的端点、字段、单据状态流转
都是业务知识——内核不该知道「入库单要先提交才能收货」。

客户端只做三件事：
1. 发请求、解析响应
2. 把 WMS 的失败**变成异常**（不吞、不降级）
3. 提供业务方法（造单据、查库存、读报表）

**为什么不用 requests**：这个仓的依赖越少越好装（服务器上
`pip install` 一次就是一次故障点）。标准库够用。

**不吞错误**：WMS 返回 `{"code": 50000, ...}` 时 HTTP 状态码还是 200。
只看状态码的话，**每一个服务端错误都会被当成成功**——
探针就会拿着一份空数据去判「通过」。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Optional


class WmsError(RuntimeError):
    """WMS 调用失败（连不上 / 非 200 / 业务 code 非 0）"""


class WmsHttpError(WmsError):
    """WMS 回了 HTTP 错误（404 / 400 / 500）

    **为什么要跟业务错误分开**：两者对探针的含义完全不同。

    - 业务错误（`code != 0`，HTTP 200）= **WMS 明确拒绝了这次请求**
      → 这往往正是要验的东西（比如「库区下还有库位时删除库区」要被拒绝）
    - HTTP 错误 = **接口不存在 / 契约对不上 / 服务炸了**
      → 404 说明**这个功能压根没有**，那是「现场没条件」，
        不是「业务没做对」；500 说明服务坏了，得先修服务。

    混成一个 WmsError 的话，探针分不出「接口没有」和「业务拒绝」，
    于是要么把没实现的功能判成失败，要么把服务故障当成通过。
    """

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        #: HTTP 状态码（0 = 不是 HTTP 层错误）
        self.status = status


class WmsClient:
    """WMS API 客户端

    Args:
        base_url: WMS 的 API 根，例如 ``http://127.0.0.1:8081/api/v1``
        timeout: 单次请求超时（秒）
    """

    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ---- 底层 ----

    def _call(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise WmsHttpError(
                f"{method} {path} → HTTP {exc.code}: {detail}", status=exc.code,
            ) from exc
        except Exception as exc:  # noqa: BLE001 - 连接类错误统一变成 WmsError
            raise WmsError(f"{method} {path} → 连不上 WMS：{exc}") from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise WmsError(
                f"{method} {path} → 响应不是 JSON：{raw[:200]}"
            ) from exc

        if not isinstance(payload, dict):
            raise WmsError(f"{method} {path} → 响应不是对象：{type(payload).__name__}")
        code = payload.get("code")
        if code != 0:
            # **业务错误也当失败**：code != 0 时 data 是 null，
            # 当成成功的话，下游拿到的是「没有数据」而不是「出错了」——
            # 探针会拿空数据判出一个假的结论。
            raise WmsError(
                f"{method} {path} → WMS 报错 code={code} "
                f"message={payload.get('message')!r}"
            )
        return payload.get("data")

    def _list(self, path: str) -> list[dict[str, Any]]:
        data = self._call("GET", path)
        if isinstance(data, dict):
            rows = data.get("list")
            return list(rows) if isinstance(rows, list) else []
        if isinstance(data, list):
            return list(data)
        return []

    # ---- 主数据 ----

    def list_skus(self) -> list[dict[str, Any]]:
        return self._list("/skus")

    def get_sku(self, sku_id: int) -> dict[str, Any]:
        return self._call("GET", f"/skus/{sku_id}")

    def create_sku(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", "/skus", payload)

    def list_locations(self) -> list[dict[str, Any]]:
        return self._list("/locations")

    # ---- 库区（仓库下的分区；库位挂在它上面）----
    #
    # **接口形状是仓里的约定**（跟仓库模块一致）：
    # GET    /zones      查询（分页）
    # POST   /zones      新增
    # PUT    /zones/{id} 编辑
    # DELETE /zones/{id} 删除
    def list_zones(self) -> list[dict[str, Any]]:
        return self._list("/zones")

    def create_zone(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", "/zones", payload)

    def get_zone(self, zone_id: int) -> dict[str, Any]:
        return self._call("GET", f"/zones/{zone_id}")

    def update_zone(self, zone_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call("PUT", f"/zones/{zone_id}", payload)

    def delete_zone(self, zone_id: int) -> dict[str, Any]:
        return self._call("DELETE", f"/zones/{zone_id}")

    # ---- 库位（挂库区靠它）----
    def create_location(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", "/locations", payload)

    def get_location(self, location_id: int) -> dict[str, Any]:
        return self._call("GET", f"/locations/{location_id}")

    def delete_location(self, location_id: int) -> dict[str, Any]:
        return self._call("DELETE", f"/locations/{location_id}")

    def list_warehouses(self) -> list[dict[str, Any]]:
        return self._list("/warehouses")

    def list_customers(self) -> list[dict[str, Any]]:
        return self._list("/customers")

    def list_inventories(self) -> list[dict[str, Any]]:
        return self._list("/inventories")

    # ---- 入库流 ----

    def create_inbound_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", "/inbound-orders", payload)

    def get_inbound_order(self, inbound_id: int) -> dict[str, Any]:
        return self._call("GET", f"/inbound-orders/{inbound_id}")

    def submit_inbound_order(self, inbound_id: int) -> dict[str, Any]:
        return self._call("POST", f"/inbound-orders/{inbound_id}/submit")

    def receive_inbound_order(self, inbound_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", f"/inbound-orders/{inbound_id}/receipts", payload)

    def list_putaway_tasks(self, inbound_id: int) -> list[dict[str, Any]]:
        return self._list(f"/putaway-tasks?inboundId={inbound_id}")

    def execute_putaway(self, task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", f"/putaway-tasks/{task_id}/execute", payload)

    # ---- 出库流 ----

    def create_outbound_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", "/outbound-orders", payload)

    def get_outbound_order(self, outbound_id: int) -> dict[str, Any]:
        return self._call("GET", f"/outbound-orders/{outbound_id}")

    def submit_outbound_order(self, outbound_id: int) -> dict[str, Any]:
        return self._call("POST", f"/outbound-orders/{outbound_id}/submit")

    def allocate_outbound_order(self, outbound_id: int) -> dict[str, Any]:
        return self._call("POST", f"/outbound-orders/{outbound_id}/allocate")

    # ---- 报表 ----

    def dashboard(self) -> dict[str, Any]:
        return self._call("GET", "/reports/dashboard")
