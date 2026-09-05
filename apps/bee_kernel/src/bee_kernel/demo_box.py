"""WMS demo BeeBox - M0 演示用 hardcoded runtime。

满足 BeeBoxProtocol：
  - list_tools() -> list[str]
  - run_tool(name, params) -> dict

WMS 库区结构（M0 简化版）：
  - 原料区（Raw）：所有 hardcoded 工具
  - 线边区（Line-side）：运行时实例（M0 同原料）
  - 成品区（Finished）/ 质检区（QC）/ 退货区（Return）：V1+
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any


class WMSDemoBox:
    """M0 演示用 BeeBox。6 个 hardcoded 工具。"""

    def list_tools(self) -> list[str]:
        return list(self._tools().keys())

    def run_tool(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        tools = self._tools()
        if name not in tools:
            raise KeyError(f"tool {name!r} not in this BeeBox; available: {list(tools.keys())}")
        t0 = time.perf_counter()
        result = tools[name](params)
        result["_elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        result["_tool"] = name
        return result

    def _tools(self) -> dict:
        return {
            "query_balances": self._query_balances,
            "reconcile_bank": self._reconcile_bank,
            "classify_expenses": self._classify_expenses,
            "generate_reports": self._generate_reports,
            "collect_evidence": self._collect_evidence,
            "request_signoff": self._request_signoff,
        }

    # === 原料区工具（hardcoded 数据）===

    def _query_balances(self, params: dict) -> dict:
        period = params.get("period", "2026-07")
        return {
            "period": period,
            "payables_count": 5,
            "payables_total": 72345.67,
            "receivables_count": 5,
            "receivables_total": 52345.43,
        }

    def _reconcile_bank(self, params: dict) -> dict:
        return {
            "matched": 42,
            "unmatched_count": 2,
            "unmatched": [
                {"date": "2026-07-12", "amount": 500.00, "reason": "凭证未到"},
                {"date": "2026-07-25", "amount": 1200.00, "reason": "金额差异"},
            ],
        }

    def _classify_expenses(self, params: dict) -> dict:
        return {
            "classified_count": 10,
            "subject": "6601 管理费用",
        }

    def _generate_reports(self, params: dict) -> dict:
        period = params.get("period", "2026-07")
        return {
            "period": period,
            "pdf_url": f"http://localhost/static/reports/{period}-balance-sheet.pdf",
            "xlsx_url": f"http://localhost/static/reports/{period}-balance-sheet.xlsx",
        }

    def _collect_evidence(self, params: dict) -> dict:
        return {
            "zip_url": f"http://localhost/static/evidence/{params.get('period', '2026-07')}.zip",
            "file_count": 87,
        }

    def _request_signoff(self, params: dict) -> dict:
        approver = params.get("approver", "manager@example.com")
        return {
            "signoff_id": f"signoff-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            "approver": approver,
            "status": "pending",
        }
