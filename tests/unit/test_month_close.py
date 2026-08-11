"""MonthCloseBox 7 模块 + 6 步 workflow 测试。"""

import pytest

from month_close import modules
from month_close.workflow import MonthCloseWorkflow


class TestModules:
    """7 个 hardcoded 模块的 schema 验证。"""

    def test_accounts_payable_query_returns_5_items(self):
        result = modules.accounts_payable_query(period="2026-07")
        assert len(result) == 5
        assert all("supplier" in r and "amount" in r and "due_date" in r for r in result)

    def test_accounts_payable_query_with_client_id(self):
        # M1 写死不区分 client_id，签名上接受
        result = modules.accounts_payable_query(period="2026-07", client_id="A001")
        assert len(result) == 5

    def test_accounts_receivable_query_returns_5_items(self):
        result = modules.accounts_receivable_query(period="2026-07")
        assert len(result) == 5
        assert all("customer" in r and "amount" in r and "invoice_date" in r for r in result)

    def test_bank_reconcile_has_matched_and_unmatched(self):
        result = modules.bank_reconcile(period="2026-07")
        assert result["matched"] == 42
        assert len(result["unmatched"]) == 2
        assert all("date" in u and "amount" in u and "reason" in u for u in result["unmatched"])

    def test_expense_classify_handles_15_vouchers_caps_at_10(self):
        vouchers = [{"id": f"v{i}"} for i in range(15)]
        result = modules.expense_classify(vouchers=vouchers)
        # M1 hardcoded：只处理前 10 条
        assert len(result) == 10
        assert all(r["subject"] == "6601 管理费用" for r in result)

    def test_expense_classify_empty_vouchers(self):
        result = modules.expense_classify(vouchers=[])
        assert result == []

    def test_report_generate_returns_urls(self):
        result = modules.report_generate(period="2026-07")
        assert "pdf_url" in result and "xlsx_url" in result
        assert "2026-07" in result["pdf_url"]

    def test_evidence_collect_returns_zip_url(self):
        result = modules.evidence_collect(period="2026-07")
        assert "zip_url" in result
        assert result["file_count"] == 87
        assert "2026-07" in result["zip_url"]

    def test_signoff_request_returns_pending(self):
        result = modules.signoff_request(
            report_url="http://x/report.pdf", approver="alice@x.com"
        )
        assert result["status"] == "pending"
        assert result["approver"] == "alice@x.com"
        assert result["signoff_id"].startswith("signoff-")


class TestWorkflow:
    """6 步 orchestrator 整体行为。"""

    @pytest.mark.asyncio
    async def test_run_returns_done_status(self):
        wf = MonthCloseWorkflow(period="2026-07")
        result = await wf.run()
        assert result["status"] == "done"
        assert result["period"] == "2026-07"

    @pytest.mark.asyncio
    async def test_run_returns_exactly_6_steps(self):
        wf = MonthCloseWorkflow(period="2026-07")
        result = await wf.run()
        assert len(result["steps"]) == 6

    @pytest.mark.asyncio
    async def test_run_step_names_match_workflow_order(self):
        wf = MonthCloseWorkflow(period="2026-07")
        result = await wf.run()
        step_names = [s["step"] for s in result["steps"]]
        assert step_names == [
            "pull_balances",
            "reconcile",
            "classify",
            "generate_reports",
            "collect_evidence",
            "request_signoff",
        ]

    @pytest.mark.asyncio
    async def test_run_result_contains_required_fields(self):
        wf = MonthCloseWorkflow(period="2026-07")
        result = await wf.run()
        r = result["result"]
        assert "report_urls" in r
        assert "evidence_url" in r
        assert "signoff_id" in r
        assert "summary" in r

    @pytest.mark.asyncio
    async def test_run_summary_counts(self):
        wf = MonthCloseWorkflow(period="2026-07")
        result = await wf.run()
        s = result["result"]["summary"]
        assert s["payables"] == 5
        assert s["receivables"] == 5
        assert s["reconcile_matched"] == 42
        assert s["reconcile_unmatched"] == 2

    @pytest.mark.asyncio
    async def test_run_custom_approver(self):
        wf = MonthCloseWorkflow(period="2026-07", approver="bob@x.com")
        result = await wf.run()
        signoff_step = result["steps"][-1]
        assert signoff_step["output"]["approver"] == "bob@x.com"
        assert signoff_step["input"]["approver"] == "bob@x.com"
