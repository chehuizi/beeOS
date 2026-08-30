"""MonthCloseBox 数据 schema（M0：纯 Pydantic 类型，无算法）。

按 BeeBox 原则：BeeBox = 数据结构，Box 只声明类型，不做决策。
V1+ 真实 adapter 替换 hardcoded 数据时，schema 保持不变。
"""

from pydantic import BaseModel, Field


# === 余额类（应付/应收公用基底） ===

class Balance(BaseModel):
    """余额条目（应付/应收共用）。"""

    party: str = Field(description="对方单位：供应商名 or 客户名")
    amount: float = Field(description="金额（元）")
    due_date: str = Field(description="到期/发票日期 YYYY-MM-DD")
    aging_days: int = Field(description="账龄天数")


class Payable(Balance):
    """应付账款条目。"""

    pass


class Receivable(Balance):
    """应收账款条目。"""

    pass


# === 凭证 / 分类 ===

class Voucher(BaseModel):
    """记账凭证。"""

    id: str
    subject: str = Field(description="会计科目，如 '6601 管理费用'")
    amount: float


# === 对账 / 报表 / 归集 / 审批 ===

class UnmatchedItem(BaseModel):
    """未达账项。"""

    date: str
    amount: float
    reason: str


class ReconcileResult(BaseModel):
    """银行对账结果。"""

    matched: int
    unmatched: list[UnmatchedItem]


class Report(BaseModel):
    """三大报表（资产负债表/利润表/现金流量表）。"""

    pdf_url: str
    xlsx_url: str


class Evidence(BaseModel):
    """凭证归集包。"""

    zip_url: str
    file_count: int


class Signoff(BaseModel):
    """审批工单。"""

    signoff_id: str
    approver: str
    report_url: str
    status: str = Field(description="pending / approved / rejected")


# === 执行轨迹 ===

class StepTrace(BaseModel):
    """单步执行轨迹（Bee 收集，写入审计）。"""

    step: str
    tool: str
    input: dict
    output: dict
    elapsed_ms: float
