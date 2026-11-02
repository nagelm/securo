import uuid

from pydantic import BaseModel


class LoanSummaryMonth(BaseModel):
    month: str  # YYYY-MM
    # Credits on the loan: repayments.
    paid: float
    # Debits on the loan that count in P&L (interest and fees).
    interest: float
    # How much the amount owed fell: credits minus debits on the loan.
    principal_reduction: float


class LoanSummary(BaseModel):
    """Per-month paid / interest / principal for one loan, read from the
    ledger (works for synced loans too). Opening-balance and ignored rows
    are left out; only posted rows count."""

    account_id: uuid.UUID
    currency: str
    months: list[LoanSummaryMonth]
    total_paid: float
    total_interest: float
    total_principal_reduction: float
