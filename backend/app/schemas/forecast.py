"""Fork: Forecast page schemas (scenarios, items, the projected series)."""
import uuid
from datetime import date as _Date
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

ForecastItemKind = Literal["one_off", "repeating", "recurring_change", "income_change", "spending_change"]
ForecastChangeMode = Literal["amount", "pause", "stop", "percent"]
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
    category_id: Optional[uuid.UUID] = None
    recurring_transaction_id: Optional[uuid.UUID] = None
    change_mode: Optional[ForecastChangeMode] = None
    percent: Optional[Decimal] = None


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
    category_id: Optional[uuid.UUID] = None
    recurring_transaction_id: Optional[uuid.UUID] = None
    change_mode: Optional[ForecastChangeMode] = None
    percent: Optional[Decimal] = None


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
    category_id: Optional[uuid.UUID] = None
    recurring_transaction_id: Optional[uuid.UUID] = None
    change_mode: Optional[str] = None
    percent: Optional[Decimal] = None
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


class ForecastResponse(BaseModel):
    currency: str
    interval: str
    start_date: _Date
    end_date: _Date
    starting_balance: float
    include_spending: bool
    base: list[ForecastPoint]
    summary: ForecastLineSummary
    scenarios: list[ForecastScenarioSeries]
    spending_estimates: list[SpendingEstimate]
