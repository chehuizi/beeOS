"""
boxes.wms_ops.wms_reader - WMS 的**只读**客户端（生产现场）

**只读**：这张盒子的价值是「说出线上实际是什么样」，
不是「改线上」。它只发 GET——写操作留在 WMS 自己的接口里。

**为什么自己写一份而不复用测试盒的客户端**：盒子之间不许互相 import
（`tests/test_layering.py` 管着这条）。运维盒要读的端点也只有两个，
抄一份三十行的只读客户端，比登记一条跨盒依赖便宜。

**不吞错误**：WMS 返回 `{"code": 50000}` 时 HTTP 状态码还是 200。
只看状态码的话，**每个服务端错误都会被当成成功**——
于是「生产上没这个数」会被读成「生产上这个数是空的」。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Optional


class ReaderError(RuntimeError):
    """读生产失败（连不上 / 非 200 / 业务 code 非 0 / 不是 JSON）"""


class WmsReader:
    """WMS 只读客户端"""

    def __init__(self, base_url: str, timeout: float = 15.0):
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout

    def _get(self, path: str) -> Any:
        if not self.base_url:
            raise ReaderError("没给生产地址")
        url = f"{self.base_url}/{path.lstrip('/')}"
        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            raise ReaderError(f"GET {path} → HTTP {exc.code}: {detail}") from exc
        except Exception as exc:  # noqa: BLE001 - 连接类错误统一变成 ReaderError
            raise ReaderError(f"GET {path} → 连不上生产：{exc}") from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ReaderError(f"GET {path} → 响应不是 JSON：{raw[:200]}") from exc
        if not isinstance(payload, dict):
            raise ReaderError(f"GET {path} → 响应不是对象")

        code = payload.get("code")
        if code is not None and code != 0:
            raise ReaderError(
                f"GET {path} → 生产报错 code={code} message={payload.get('message')!r}"
            )
        # /api/v1/health 不走统一包络（扁平的 {"status": "UP"}）
        if "data" in payload:
            return payload.get("data")
        return payload

    def dashboard(self) -> dict[str, Any]:
        """库存看板（业务现场的那些数）"""
        data = self._get("/reports/dashboard")
        return data if isinstance(data, dict) else {}

    def health(self) -> dict[str, Any]:
        """健康（不走统一包络）"""
        data = self._get("/health")
        return data if isinstance(data, dict) else {}


def observed_at() -> str:
    """这次读生产的时间（UTC ISO8601）"""
    return datetime.now(timezone.utc).isoformat()
