"""Forecast schemas. Upstream: detected recurring streams and the cash flow
simulation. Fork: the Forecast page (scenarios, items, the projected series)."""
import uuid
from datetime import date
from datetime import date as _Date
from decimal import Decimal
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class CadenceEnum(str, Enum):
    WEEKLY = "weekly"
    BI_WEEKLY = "bi_weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    SEMI_ANNUAL = "semi_annual"
    ANNUAL = "annual"
    IRREGULAR = "irregular"


class SubscriptionStatusEnum(str, Enum):
    ACTIVE = "active"
    LAPSED = "lapsed"
    PENDING = "pending"


class FlowDirectionEnum(str, Enum):
    INFLOW = "inflow"
    OUTFLOW = "outflow"


class DetectedRecurringItem(BaseModel):
    merchant_name: str
    normalized_key: str
    direction: FlowDirectionEnum
    cadence: CadenceEnum
    average_interval_days: float
    occurrence_count: int
    first_seen_date: date
    last_seen_date: date
    next_expected_date: date
    average_amount: Decimal
    currency: str
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    is_subscription: bool
    status: SubscriptionStatusEnum
    estimated_annual_impact: Decimal
    category_id: Optional[uuid.UUID] = None
    account_id: Optional[uuid.UUID] = None
    sample_transaction_ids: list[uuid.UUID] = Field(default_factory=list)


class RecurringDetectionResponse(BaseModel):
    workspace_id: uuid.UUID
    total_detected: int
    active_subscriptions_count: int
    total_monthly_subscription_cost: Decimal
    total_annual_subscription_cost: Decimal
    items: list[DetectedRecurringItem]
    # Currencies of transactions left out of the amounts because no FX rate to
    # the workspace currency was available.
    unconverted_currencies: list[str] = Field(default_factory=list)


class DailyForecastPoint(BaseModel):
    date: date
    starting_balance: Decimal
    recurring_inflow: Decimal
    recurring_outflow: Decimal
    discretionary_burn: Decimal
    net_change: Decimal
    ending_balance: Decimal
    events: list[str] = Field(default_factory=list)


class ForecastSummary(BaseModel):
    horizon_days: int
    start_date: date
    end_date: date
    starting_liquid_balance: Decimal
    # What the credit cards stand at, signed like the account screen (debt is
    # negative). Not part of the liquid balance: it is not due today.
    credit_card_balance: Decimal = Decimal("0.00")
    projected_ending_balance: Decimal
    lowest_projected_balance: Decimal
    lowest_balance_date: date
    total_projected_inflow: Decimal
    total_projected_outflow: Decimal
    total_projected_discretionary: Decimal
    net_cashflow: Decimal
    runway_days: Optional[int] = None
    has_shortfall_risk: bool
    first_shortfall_date: Optional[date] = None
    # Currencies of balances or bills left out because no FX rate to the
    # workspace currency was available.
    unconverted_currencies: list[str] = Field(default_factory=list)


class CashflowForecastResponse(BaseModel):
    workspace_id: uuid.UUID
    summary: ForecastSummary
    daily_trajectory: list[DailyForecastPoint]


# --- Fork: the Forecast page ---

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


class ForecastBudgetChoices(BaseModel):
    """Fork: the budgets the forecast applies (opt-in, none by default):
    category budgets by category, budget groups by group."""

    category_ids: list[uuid.UUID] = []
    budget_group_ids: list[uuid.UUID] = []


class ForecastBudgetUse(BaseModel):
    """Fork: one chosen budget in one month of the forecast. ``committed`` is
    what recurring and expected payments categorised there already project
    from tomorrow; ``remainder`` what is left for everyday spending (both
    for the days still to come in the month)."""

    category_id: Optional[uuid.UUID] = None
    budget_group_id: Optional[uuid.UUID] = None
    name: str
    month: _Date
    amount: float
    committed: float
    remainder: float


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
    # Fork: a transfer between own accounts (or a loan payment): the whole
    # payment from ``account_id`` into this account.
    transfer_account_id: Optional[uuid.UUID] = None


class ForecastResponse(BaseModel):
    currency: str
    interval: str
    start_date: _Date
    end_date: _Date
    starting_balance: float
    include_spending: bool
    # Fork: the accounts the line covers (the cash-flow accounts, or the
    # account view's share of them).
    account_ids: list[uuid.UUID] = []
    # The real cash-and-cards balance from (today − horizon) to today, at the
    # same interval; its last point is today at ``starting_balance``.
    actuals: list[ForecastPoint] = []
    base: list[ForecastPoint]
    summary: ForecastLineSummary
    scenarios: list[ForecastScenarioSeries]
    spending_estimates: list[SpendingEstimate]
    loans: list[LoanForecast] = []
    recurring_payments: list[ForecastRecurringPayment] = []
    # Fork: the chosen budgets, month by month (empty unless some are chosen).
    budget_uses: list[ForecastBudgetUse] = []
