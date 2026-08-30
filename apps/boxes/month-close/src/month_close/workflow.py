"""MonthCloseBox 工作流 - 6 步声明（数据，非算法）。

设计原则（BeeBox = 数据结构）：
- WORKFLOW 列表只是 step 顺序声明
- 实际执行由 bee/orchestrator.py 调度
- 每个 step 的函数在这里实现（调 adapters），但 step-to-step 的串联由 Bee 决定

M0 写死 6 步；V1+ 可以从 manifest.yaml 加载或 LLM 决定。
"""

from __future__ import annotations

from month_close import adapters


# === Manifest（供 Bee 加载）===

MANIFEST: dict = {
    "box_type": "month_close",
    "name": "MonthCloseBox",
    "version": "0.1.0",
    "description": "会计月结自动化",
    "schemas": [
        "Payable", "Receivable", "Voucher", "ReconcileResult",
        "Report", "Evidence", "Signoff",
    ],
    "tools": [
        "accounts_payable_query", "accounts_receivable_query",
        "bank_reconcile", "expense_classify",
        "report_generate", "evidence_collect", "signoff_request",
    ],
}


# === 6 步工作流（M0 写死）===

WORKFLOW: list[dict] = [
    {
        "name": "pull_balances",
        "tool": "_step_pull_balances",
        "description": "拉余额（应付+应收）",
    },
    {
        "name": "reconcile",
        "tool": "_step_reconcile",
        "description": "银行对账",
    },
    {
        "name": "classify",
        "tool": "_step_classify",
        "description": "费用分类",
    },
    {
        "name": "generate_reports",
        "tool": "_step_generate_reports",
        "description": "生成三大报表",
    },
    {
        "name": "collect_evidence",
        "tool": "_step_collect_evidence",
        "description": "凭证归集",
    },
    {
        "name": "request_signoff",
        "tool": "_step_request_signoff",
        "description": "发起审批",
    },
]


# === 单步实现（数据组合，不做决策）===

def _step_pull_balances(context: dict, prev: dict) -> dict:
    """Step 1: 拉余额。"""
    period = context["period"]
    return {
        "payables": adapters.accounts_payable_query(period=period),
        "receivables": adapters.accounts_receivable_query(period=period),
    }


def _step_reconcile(context: dict, prev: dict) -> dict:
    """Step 2: 银行对账。"""
    return adapters.bank_reconcile(period=context["period"])


def _step_classify(context: dict, prev: dict) -> dict:
    """Step 3: 凭证分类（拿 Step 1 的数据做输入）。"""
    # M0 简化：不管 prev["pull_balances"]，写死 15 条假凭证
    vouchers = [{"id": f"v{i}"} for i in range(15)]
    classified = adapters.expense_classify(vouchers=vouchers)
    return {"classified_count": len(classified)}


def _step_generate_reports(context: dict, prev: dict) -> dict:
    """Step 4: 生成三大报表。"""
    return adapters.report_generate(period=context["period"])


def _step_collect_evidence(context: dict, prev: dict) -> dict:
    """Step 5: 凭证归集。"""
    return adapters.evidence_collect(period=context["period"])


def _step_request_signoff(context: dict, prev: dict) -> dict:
    """Step 6: 发起审批（依赖 Step 4 的 report_url）。"""
    reports = prev.get("generate_reports", {})
    report_url = reports.get("pdf_url", "")
    return adapters.signoff_request(
        report_url=report_url,
        approver=context.get("approver", "manager@example.com"),
    )


# === Step dispatcher（Bee 调用入口）===

_STEP_FUNCS = {
    "_step_pull_balances": _step_pull_balances,
    "_step_reconcile": _step_reconcile,
    "_step_classify": _step_classify,
    "_step_generate_reports": _step_generate_reports,
    "_step_collect_evidence": _step_collect_evidence,
    "_step_request_signoff": _step_request_signoff,
}


def run_step(step_name: str, context: dict, prev_outputs: dict) -> dict:
    """执行单步。Bee 通过此入口调 Box。

    Args:
        step_name: 步骤名（"pull_balances" / "reconcile" / ...）
        context: 任务上下文（period, client_ids, approver, ...）
        prev_outputs: 之前步骤的输出，dict[step_name -> output]

    Returns:
        该步的输出 dict
    """
    step = next((s for s in WORKFLOW if s["name"] == step_name), None)
    if step is None:
        raise ValueError(f"Unknown step: {step_name}. Available: {[s['name'] for s in WORKFLOW]}")

    func = _STEP_FUNCS[step["tool"]]
    return func(context, prev_outputs)


def list_steps() -> list[str]:
    """返回所有 step 名（按 WORKFLOW 顺序）。"""
    return [s["name"] for s in WORKFLOW]
