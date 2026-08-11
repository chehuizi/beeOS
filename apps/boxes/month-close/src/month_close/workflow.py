"""月结工作流 - 6 步固定流程（M1 写死，不调 LLM 拆解）。

对应 [技术架构 §4.4] 和 box.yaml workflow 段。
"""

from pydantic import BaseModel, Field

from beeos_core.logging import get_logger

from month_close import modules

logger = get_logger(__name__)


class StepResult(BaseModel):
    """单步执行结果。"""

    step: str
    module: str
    input: dict
    output: dict


class MonthCloseWorkflow(BaseModel):
    """月结工作流（6 步写死版）。

    6 步：
      1. pull_balances (应付 + 应收)
      2. reconcile (银行对账)
      3. classify (费用分类)
      4. generate_reports (出三大报表)
      5. collect_evidence (凭证归集)
      6. request_signoff (发起审批)
    """

    period: str
    client_ids: list[str] = Field(default_factory=list)
    approver: str = "manager@example.com"

    async def run(self) -> dict:
        """执行 6 步月结流程。

        Returns:
            dict 含 status / period / steps (6 步 trace) / result (最终输出)
        """
        steps: list[StepResult] = []
        logger.info("month_close.start", period=self.period)

        # Step 1: 拉余额（应付 + 应收）
        payables = modules.accounts_payable_query(period=self.period)
        receivables = modules.accounts_receivable_query(period=self.period)
        steps.append(
            StepResult(
                step="pull_balances",
                module="accounts_payable_query+accounts_receivable_query",
                input={"period": self.period, "client_ids": self.client_ids},
                output={
                    "payables_count": len(payables),
                    "receivables_count": len(receivables),
                    "payables_total": sum(p["amount"] for p in payables),
                    "receivables_total": sum(r["amount"] for r in receivables),
                },
            )
        )

        # Step 2: 银行对账
        reconcile = modules.bank_reconcile(period=self.period)
        steps.append(
            StepResult(
                step="reconcile",
                module="bank_reconcile",
                input={"period": self.period},
                output=reconcile,
            )
        )

        # Step 3: 费用分类（拿 Step 1 模拟的 15 条凭证做输入）
        vouchers = [{"id": f"v{i}"} for i in range(15)]
        classified = modules.expense_classify(vouchers=vouchers)
        steps.append(
            StepResult(
                step="classify",
                module="expense_classify",
                input={"voucher_count": len(vouchers)},
                output={"classified_count": len(classified)},
            )
        )

        # Step 4: 生成报表
        reports = modules.report_generate(period=self.period)
        steps.append(
            StepResult(
                step="generate_reports",
                module="report_generate",
                input={"period": self.period},
                output=reports,
            )
        )

        # Step 5: 凭证归集
        evidence = modules.evidence_collect(period=self.period)
        steps.append(
            StepResult(
                step="collect_evidence",
                module="evidence_collect",
                input={"period": self.period},
                output=evidence,
            )
        )

        # Step 6: 发起审批
        signoff = modules.signoff_request(
            report_url=reports["pdf_url"],
            approver=self.approver,
        )
        steps.append(
            StepResult(
                step="request_signoff",
                module="signoff_request",
                input={"report_url": reports["pdf_url"], "approver": self.approver},
                output=signoff,
            )
        )

        logger.info("month_close.done", period=self.period, steps=len(steps))
        return {
            "status": "done",
            "period": self.period,
            "steps": [s.model_dump() for s in steps],
            "result": {
                "report_urls": reports,
                "evidence_url": evidence["zip_url"],
                "signoff_id": signoff["signoff_id"],
                "summary": {
                    "payables": len(payables),
                    "receivables": len(receivables),
                    "reconcile_matched": reconcile["matched"],
                    "reconcile_unmatched": len(reconcile["unmatched"]),
                },
            },
        }
