import uuid
from datetime import date as _Date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class LoanPaymentCreate(BaseModel):
    """Record one payment on a manual loan.

    Either `cash_account_id` (the payment writes the cash debit) or
    `existing_transaction_id` (it pairs a debit that already exists on the
    paying account, e.g. a synced or imported one). `date` is required with
    `cash_account_id`; when pairing it defaults to the existing debit's date.
    Amounts are validated by the service (two decimals, total > 0,
    0 <= interest <= total) so a bad amount is a 400 like every other rule.
    """

    loan_account_id: uuid.UUID
    date: Optional[_Date] = None
    total_amount: Decimal
    interest_amount: Decimal = Decimal("0")
    interest_category_id: Optional[uuid.UUID] = None
    cash_account_id: Optional[uuid.UUID] = None
    existing_transaction_id: Optional[uuid.UUID] = None
    description: Optional[str] = None
    notes: Optional[str] = None


class LoanPaymentUpdate(BaseModel):
    """Change a recorded payment. Omitted fields keep their value.

    A payment that paired an existing debit keeps the bank's date and total;
    only its interest split, interest category and description may change.
    """

    date: Optional[_Date] = None
    total_amount: Optional[Decimal] = None
    interest_amount: Optional[Decimal] = None
    interest_category_id: Optional[uuid.UUID] = None
    description: Optional[str] = None


class LoanPaymentRead(BaseModel):
    """A recorded payment with amounts derived from its rows.

    `total` is the cash leg's amount, `interest` the interest row's amount
    (0 without one) and `principal = total - interest`. Display only:
    nothing stores principal.
    """

    id: uuid.UUID
    loan_account_id: uuid.UUID
    cash_account_id: uuid.UUID
    cash_transaction_id: uuid.UUID
    loan_transaction_id: uuid.UUID
    interest_transaction_id: Optional[uuid.UUID] = None
    interest_category_id: Optional[uuid.UUID] = None
    cash_leg_created: bool
    recurring_transaction_id: Optional[uuid.UUID] = None
    date: _Date
    description: str
    currency: str
    total: Decimal
    interest: Decimal
    principal: Decimal
    created_at: datetime


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
    # False for loans that sync their own rows: the manual payment form is
    # not offered there (it would double count the synced rows).
    manual_payments_allowed: bool
    months: list[LoanSummaryMonth]
    total_paid: float
    total_interest: float
    total_principal_reduction: float
