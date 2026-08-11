"""MonthCloseBox 7 个模块 - M1 hardcoded 实现。

每个模块返回固定 JSON（per box.yaml schema）。
M1 不调外部 API（不连金蝶/用友），纯 hardcoded。
V1+ 替换为真实 adapter。
"""

from datetime import datetime


def accounts_payable_query(period: str, client_id: str | None = None) -> list[dict]:
    """查应付账款（M1 hardcoded：5 条假应付）。"""
    return [
        {
            "supplier": f"供应商-{i:03d}",
            "amount": 12_345.67 + i * 1000,
            "due_date": f"{period}-15",
            "aging_days": 30 + i,
        }
        for i in range(1, 6)
    ]


def accounts_receivable_query(period: str, client_id: str | None = None) -> list[dict]:
    """查应收账款（M1 hardcoded：5 条假应收）。"""
    return [
        {
            "customer": f"客户-{i:03d}",
            "amount": 8_765.43 + i * 500,
            "invoice_date": f"{period}-05",
            "aging_days": 15 + i,
        }
        for i in range(1, 6)
    ]


def bank_reconcile(period: str) -> dict:
    """银行对账（M1 hardcoded：42 matched + 2 unmatched）。"""
    return {
        "matched": 42,
        "unmatched": [
            {"date": f"{period}-12", "amount": 500.00, "reason": "凭证未到"},
            {"date": f"{period}-25", "amount": 1_200.00, "reason": "金额差异"},
        ],
    }


def expense_classify(vouchers: list[dict]) -> list[dict]:
    """费用按科目分类（M1 hardcoded：每条凭证 → "6601 管理费用"）。"""
    return [
        {
            "voucher_id": v.get("id", i),
            "subject": "6601 管理费用",
            "amount": 100.0 + i * 50,
        }
        for i, v in enumerate(vouchers[:10])
    ]


def report_generate(period: str) -> dict:
    """生成三大报表（M1 hardcoded：返回 URL 占位）。"""
    return {
        "pdf_url": f"http://101.37.146.194/static/reports/{period}-balance-sheet.pdf",
        "xlsx_url": f"http://101.37.146.194/static/reports/{period}-balance-sheet.xlsx",
    }


def evidence_collect(period: str) -> dict:
    """凭证归集（M1 hardcoded：返回 URL 占位 + 固定 87 个文件）。"""
    return {
        "zip_url": f"http://101.37.146.194/static/evidence/{period}.zip",
        "file_count": 87,
    }


def signoff_request(report_url: str, approver: str) -> dict:
    """发起审批（M1 hardcoded：返回 signoff_id + pending 状态）。"""
    return {
        "signoff_id": f"signoff-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
        "approver": approver,
        "report_url": report_url,
        "status": "pending",
    }
