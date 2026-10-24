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
            raise WmsError(f"{method} {path} → HTTP {exc.code}: {detail}") from exc
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
