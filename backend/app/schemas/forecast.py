"""Fork: Forecast page schemas (scenarios, items, the projected series)."""
import uuid
from datetime import date as _Date
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

ForecastItemKind = Literal[
    "one_off", "repeating", "recurring_change", "income_change", "spending_change", "loan_change"
]
ForecastChangeMode = Literal["amount", "pause", "stop", "percent", "rate", "lump_sum"]
ForecastFrequency = Literal["weekly", "biweekly", "monthly", "quarterly", "semiannual", "yearly"]
TxType = Literal["debit", "credit"]
HexColor = Field(pattern=r"^#[0-9A-Fa-f]{6}$")


class ForecastScenarioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    color: str = HexColor
    is_enabled: bool = True
    position: Optional[int] = None


class ForecastScenarioUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    color: Optional[str] = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    is_enabled: Optional[bool] = None
    position: Optional[int] = None


class ForecastScenarioRead(BaseModel):
    id: uuid.UUID
    name: str
    color: str
    is_enabled: bool
    position: int

    model_config = ConfigDict(from_attributes=True)


class ForecastItemCreate(BaseModel):
    scenario_id: Optional[uuid.UUID] = None
    kind: ForecastItemKind = "one_off"
    description: str = Field(default="", max_length=500)
    amount: Optional[Decimal] = None
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    type: Optional[TxType] = None
    date: Optional[_Date] = None
    start_date: Optional[_Date] = None
    end_date: Optional[_Date] = None
    frequency: Optional[ForecastFrequency] = None
    account_id: Optional[uuid.UUID] = None
    # Fork: an expected transfer's to-account (``account_id`` is the from-account).
    transfer_account_id: Optional[uuid.UUID] = None
    category_id: Optional[uuid.UUID] = None
    recurring_transaction_id: Optional[uuid.UUID] = None
    change_mode: Optional[ForecastChangeMode] = None
    percent: Optional[Decimal] = None
    recalculate_repayment: Optional[bool] = None


class ForecastItemUpdate(BaseModel):
    description: Optional[str] = Field(default=None, max_length=500)
    amount: Optional[Decimal] = None
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    type: Optional[TxType] = None
    date: Optional[_Date] = None
    start_date: Optional[_Date] = None
    end_date: Optional[_Date] = None
    frequency: Optional[ForecastFrequency] = None
    account_id: Optional[uuid.UUID] = None
    transfer_account_id: Optional[uuid.UUID] = None
    category_id: Optional[uuid.UUID] = None
    recurring_transaction_id: Optional[uuid.UUID] = None
    change_mode: Optional[ForecastChangeMode] = None
    percent: Optional[Decimal] = None
    recalculate_repayment: Optional[bool] = None


class ForecastItemMatchRead(BaseModel):
    """Fork: one matched occurrence of a repeating expected payment."""

    occurrence_date: _Date
    transaction_id: uuid.UUID

    model_config = ConfigDict(from_attributes=True)


class ForecastItemRead(BaseModel):
    id: uuid.UUID
    scenario_id: Optional[uuid.UUID] = None
    kind: str
    description: str
    amount: Optional[Decimal] = None
    currency: str
    type: Optional[str] = None
    date: Optional[_Date] = None
    start_date: Optional[_Date] = None
    end_date: Optional[_Date] = None
    frequency: Optional[str] = None
    account_id: Optional[uuid.UUID] = None
    transfer_account_id: Optional[uuid.UUID] = None
    category_id: Optional[uuid.UUID] = None
    recurring_transaction_id: Optional[uuid.UUID] = None
    change_mode: Optional[str] = None
    percent: Optional[Decimal] = None
    recalculate_repayment: Optional[bool] = None
    matched_transaction_id: Optional[uuid.UUID] = None
    # Fork: a repeating expected payment's matched occurrences, and how many
    # payments it makes from start to end (None when open-ended).
    matches: list[ForecastItemMatchRead] = []
    occurrence_count: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


class ForecastPoint(BaseModel):
    date: _Date
    value: float


class ForecastLineSummary(BaseModel):
    end_balance: float
    lowest_balance: float
    lowest_date: _Date


class ForecastScenarioSeries(BaseModel):
    id: uuid.UUID
    name: str
    color: str
    points: list[ForecastPoint]
    summary: ForecastLineSummary
    # End balance less the base line's end balance.
    difference: float


class SpendingEstimate(BaseModel):
    """A category's average monthly net spending, the base for spending_change."""

    category_id: uuid.UUID
    name: str
    color: str
    monthly_amount: float


class LoanPoint(BaseModel):
    """A loan's balance owed at ``date`` and what the repayments since the
    previous point paid in interest and in principal (lump sums included)."""

    date: _Date
    balance: float
    interest: float
    principal: float


class LoanLine(BaseModel):
    points: list[LoanPoint]
    # None: not paid off within 100 years (the repayment doesn't cover the interest).
    payoff_date: Optional[_Date] = None
    # From today until payoff, beyond the forecast horizon.
    total_interest: float
    # The regular repayment at the end of the horizon (after any change).
    repayment: float
    # Fork: the balance owed month by month over the loan's whole term, to the
    # latest payoff among the loan's lines, so a scenario shows how much
    # sooner it ends. Empty when no line pays off within the cap.
    term_points: list[LoanPoint] = []


class LoanScenarioLine(LoanLine):
    scenario_id: uuid.UUID
    name: str
    color: str
    # Base total interest less this line's; positive = saved. None when
    # either line never pays off.
    interest_saved: Optional[float] = None
    # Payoff date less the base's, in days; negative = earlier.
    payoff_change_days: Optional[int] = None


class LoanForecast(BaseModel):
    account_id: uuid.UUID
    name: str
    currency: str
    balance: float  # owed today, positive
    rate: Optional[float] = None
    frequency: Optional[str] = None
    # True: loan_details lack a table loan's rate, repayment and frequency
    # (e.g. a revolving loan), so there is no projection.
    missing_details: bool
    # Owed at each history point (as far back as the forecast looks ahead), ending today.
    actuals: list[ForecastPoint] = []
    base: Optional[LoanLine] = None
    scenarios: list[LoanScenarioLine] = []


class ForecastRecurringPayment(BaseModel):
    """Fork: one dated payment of a recurring schedule inside the forecast,
    the same rows the base line counts (owner 2026-10-15: listed with the
    expected payments)."""
    recurring_transaction_id: uuid.UUID
    date: _Date
    description: str
    amount: float
    type: str
    currency: str
    account_id: Optional[uuid.UUID] = None


class ForecastResponse(BaseModel):
    currency: str
    interval: str
    start_date: _Date
    end_date: _Date
    starting_balance: float
    include_spending: bool
    # The real cash-and-cards balance from (today − horizon) to today, at the
    # same interval; its last point is today at ``starting_balance``.
    actuals: list[ForecastPoint] = []
    base: list[ForecastPoint]
    summary: ForecastLineSummary
    scenarios: list[ForecastScenarioSeries]
    spending_estimates: list[SpendingEstimate]
    loans: list[LoanForecast] = []
    recurring_payments: list[ForecastRecurringPayment] = []
