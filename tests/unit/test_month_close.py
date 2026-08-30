"""MonthCloseBox 测试（M0：schema + adapters + workflow 声明）。"""

import pytest

from month_close import MANIFEST, WORKFLOW, adapters, list_steps, run_step


# === Manifest ===

class TestManifest:
    def test_box_type(self):
        assert MANIFEST["box_type"] == "month_close"

    def test_has_7_schemas(self):
        assert len(MANIFEST["schemas"]) == 7

    def test_has_7_tools(self):
        assert len(MANIFEST["tools"]) == 7


# === Adapters（数据工具）===

class TestAdapters:
    """7 个 hardcoded adapter 的 schema 验证。"""

    def test_accounts_payable_query_returns_5_items(self):
        result = adapters.accounts_payable_query(period="2026-07")
        assert len(result) == 5
        assert all("party" in r and "amount" in r and "due_date" in r for r in result)

    def test_accounts_payable_query_with_client_id(self):
        # M0 写死不区分 client_id，签名上接受
        result = adapters.accounts_payable_query(period="2026-07", client_id="A001")
        assert len(result) == 5

    def test_accounts_receivable_query_returns_5_items(self):
        result = adapters.accounts_receivable_query(period="2026-07")
        assert len(result) == 5
        assert all("party" in r and "amount" in r and "due_date" in r for r in result)

    def test_bank_reconcile_has_matched_and_unmatched(self):
        result = adapters.bank_reconcile(period="2026-07")
        assert result["matched"] == 42
        assert len(result["unmatched"]) == 2
        assert all("date" in u and "amount" in u and "reason" in u for u in result["unmatched"])

    def test_expense_classify_handles_15_vouchers_caps_at_10(self):
        vouchers = [{"id": f"v{i}"} for i in range(15)]
        result = adapters.expense_classify(vouchers=vouchers)
        # M0 hardcoded：只处理前 10 条
        assert len(result) == 10
        assert all(r["subject"] == "6601 管理费用" for r in result)

    def test_expense_classify_empty_vouchers(self):
        result = adapters.expense_classify(vouchers=[])
        assert result == []

    def test_report_generate_returns_urls(self):
        result = adapters.report_generate(period="2026-07")
        assert "pdf_url" in result and "xlsx_url" in result
        assert "2026-07" in result["pdf_url"]

    def test_evidence_collect_returns_zip_url(self):
        result = adapters.evidence_collect(period="2026-07")
        assert "zip_url" in result
        assert result["file_count"] == 87
        assert "2026-07" in result["zip_url"]

    def test_signoff_request_returns_pending(self):
        result = adapters.signoff_request(
            report_url="http://x/report.pdf", approver="alice@x.com"
        )
        assert result["status"] == "pending"
        assert result["approver"] == "alice@x.com"
        assert result["signoff_id"].startswith("signoff-")


# === Workflow（声明 + run_step）===

class TestWorkflow:
    """6 步工作流声明和单步执行。"""

    def test_workflow_has_6_steps(self):
        assert len(WORKFLOW) == 6
        assert list_steps() == [
            "pull_balances", "reconcile", "classify",
            "generate_reports", "collect_evidence", "request_signoff",
        ]

    def test_each_step_has_name_tool_description(self):
        for step in WORKFLOW:
            assert "name" in step
            assert "tool" in step
            assert "description" in step

    def test_run_step_pull_balances(self):
        out = run_step("pull_balances", {"period": "2026-07"}, {})
        assert "payables" in out
        assert "receivables" in out
        assert len(out["payables"]) == 5
        assert len(out["receivables"]) == 5

    def test_run_step_reconcile(self):
        out = run_step("reconcile", {"period": "2026-07"}, {})
        assert out["matched"] == 42
        assert len(out["unmatched"]) == 2

    def test_run_step_generate_reports(self):
        out = run_step("generate_reports", {"period": "2026-07"}, {})
        assert "pdf_url" in out
        assert "xlsx_url" in out

    def test_run_step_request_signoff_uses_prev_reports(self):
        """Step 6 依赖 Step 4 的 report_url。"""
        prev = {
            "generate_reports": {"pdf_url": "http://test/report.pdf", "xlsx_url": "x"},
        }
        out = run_step("request_signoff", {"period": "2026-07", "approver": "bob@x.com"}, prev)
        assert out["report_url"] == "http://test/report.pdf"
        assert out["approver"] == "bob@x.com"

    def test_run_step_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown step"):
            run_step("nonexistent", {"period": "2026-07"}, {})


# === Schema ===

class TestSchema:
    """Pydantic schema 验证（数据契约）。"""

    def test_payable_accepts_valid(self):
        from month_close.schema import Payable
        p = Payable(party="X", amount=100.0, due_date="2026-07-15", aging_days=30)
        assert p.party == "X"
        assert p.amount == 100.0

    def test_balance_serialization(self):
        from month_close.schema import Balance
        b = Balance(party="Y", amount=200.0, due_date="2026-08-01", aging_days=60)
        d = b.model_dump()
        assert d["party"] == "Y"
        assert d["aging_days"] == 60
