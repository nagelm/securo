import uuid
from datetime import date as _Date, datetime
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: The recurring-transaction frequencies a goal's periodic target can use
#: (`biweekly` is a fortnight).
ContributionPeriod = Literal["weekly", "biweekly", "monthly"]


def check_targets(
    target_amount: Optional[Decimal],
    contribution_amount: Optional[Decimal],
    contribution_period: Optional[str],
) -> None:
    """A goal needs a total, a periodic target or both; a periodic target
    needs both its amount and its period."""
    if (contribution_amount is None) != (contribution_period is None):
        raise ValueError("contribution_amount and contribution_period must be set together")
    if target_amount is None and contribution_amount is None:
        raise ValueError("a goal needs a target_amount, a periodic contribution target, or both")


class GoalCreate(BaseModel):
    name: str
    # Optional for an ongoing savings goal that only has a periodic target.
    target_amount: Optional[Decimal] = None
    contribution_amount: Optional[Decimal] = Field(default=None, gt=0)
    contribution_period: Optional[ContributionPeriod] = None
    current_amount: Decimal = Decimal("0")
    currency: str = "USD"
    target_date: Optional[_Date] = None
    tracking_type: str = "manual"
    account_id: Optional[uuid.UUID] = None
    asset_id: Optional[uuid.UUID] = None
    asset_group_id: Optional[uuid.UUID] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    metadata_json: Optional[Any] = None

    @field_validator("tracking_type")
    @classmethod
    def validate_tracking_type(cls, v: str) -> str:
        if v not in ("manual", "account", "asset", "asset_group", "net_worth"):
            raise ValueError("tracking_type must be manual, account, asset, asset_group, or net_worth")
        return v

    @model_validator(mode="after")
    def validate_targets(self) -> "GoalCreate":
        check_targets(self.target_amount, self.contribution_amount, self.contribution_period)
        return self


class GoalBulkCreate(BaseModel):
    """Several goals in one go, e.g. one per savings account."""

    goals: list[GoalCreate] = Field(min_length=1, max_length=50)


class GoalUpdate(BaseModel):
    name: Optional[str] = None
    target_amount: Optional[Decimal] = None
    contribution_amount: Optional[Decimal] = Field(default=None, gt=0)
    contribution_period: Optional[ContributionPeriod] = None
    current_amount: Optional[Decimal] = None
    currency: Optional[str] = None
    target_date: Optional[_Date] = None
    tracking_type: Optional[str] = None
    account_id: Optional[uuid.UUID] = None
    asset_id: Optional[uuid.UUID] = None
    asset_group_id: Optional[uuid.UUID] = None
    status: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    position: Optional[int] = None
    metadata_json: Optional[Any] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("active", "completed", "paused", "archived"):
            raise ValueError("status must be active, completed, paused, or archived")
        return v

    @field_validator("tracking_type")
    @classmethod
    def validate_tracking_type(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("manual", "account", "asset", "asset_group", "net_worth"):
            raise ValueError("tracking_type must be manual, account, asset, asset_group, or net_worth")
        return v


class ContributionPeriodRead(BaseModel):
    start: _Date
    end: _Date
    contributed: Decimal
    target: Decimal
    # met / over: the target was reached; short: a finished period that
    # fell short; pending: the current period, not reached yet.
    status: Literal["met", "over", "short", "pending"]
    current: bool = False


class ContributionProgressRead(BaseModel):
    period: ContributionPeriod
    target: Decimal
    current: ContributionPeriodRead
    # The last ten finished periods and the current one: oldest first,
    # current last.
    history: list[ContributionPeriodRead]
    streak: int
    # Ahead or behind the plan since money last left the account (the first
    # deposit, when it never has): deposits since then, what the target asked
    # for since then, and saved - planned (negative: behind). All None while
    # the account has had nothing in or out.
    since: Optional[_Date] = None
    saved_since: Optional[Decimal] = None
    planned_since: Optional[Decimal] = None
    ahead_by: Optional[Decimal] = None


class ContributionSuggestionRead(BaseModel):
    amount: Decimal
    period: ContributionPeriod
    currency: str
    source: Literal["recurring", "history"]
    recurring_transaction_id: Optional[uuid.UUID] = None
    description: Optional[str] = None


class GoalRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    target_amount: Optional[Decimal] = None
    contribution_amount: Optional[Decimal] = None
    contribution_period: Optional[ContributionPeriod] = None
    current_amount: Decimal
    currency: str
    target_amount_primary: Optional[Decimal] = None
    current_amount_primary: Optional[Decimal] = None
    target_date: Optional[_Date] = None
    tracking_type: str
    account_id: Optional[uuid.UUID] = None
    asset_id: Optional[uuid.UUID] = None
    asset_group_id: Optional[uuid.UUID] = None
    status: str
    icon: Optional[str] = None
    color: Optional[str] = None
    position: int
    metadata_json: Optional[Any] = None
    created_at: datetime
    updated_at: datetime

    # Computed fields
    percentage: float = 0
    monthly_contribution: Optional[float] = None
    on_track: Optional[str] = None  # ahead, on_track, behind, overdue, achieved
    # Contributed vs the periodic target, for a goal that tracks an account
    # and has one. None otherwise.
    contribution_progress: Optional[ContributionProgressRead] = None

    # Linked account/asset info
    account_name: Optional[str] = None
    asset_name: Optional[str] = None
    asset_group_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class GoalSummary(BaseModel):
    id: uuid.UUID
    name: str
    target_amount: Optional[Decimal] = None
    contribution_amount: Optional[Decimal] = None
    contribution_period: Optional[ContributionPeriod] = None
    period_contributed: Optional[Decimal] = None
    period_status: Optional[str] = None
    contribution_streak: Optional[int] = None
    current_amount: Decimal
    currency: str
    target_date: Optional[_Date] = None
    status: str
    icon: Optional[str] = None
    color: Optional[str] = None
    percentage: float = 0
    monthly_contribution: Optional[float] = None
    on_track: Optional[str] = None
