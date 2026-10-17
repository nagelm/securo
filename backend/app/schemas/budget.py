import uuid
from datetime import date as _Date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class BudgetCreate(BaseModel):
    category_id: uuid.UUID
    amount: Decimal
    month: _Date  # First day of month
    is_recurring: bool = False
    # Fork: a recurring budget's last month (inclusive); None = open-ended.
    end_month: Optional[_Date] = None


class BudgetUpdate(BaseModel):
    amount: Optional[Decimal] = None
    effective_month: Optional[_Date] = None
    # Fork: null clears it (open-ended again).
    end_month: Optional[_Date] = None


class BudgetRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    category_id: uuid.UUID
    amount: Decimal
    month: _Date
    is_recurring: bool
    end_month: Optional[_Date] = None  # Fork

    model_config = ConfigDict(from_attributes=True)


class BudgetVsActual(BaseModel):
    category_id: uuid.UUID
    category_name: str
    category_icon: str
    category_color: str
    group_id: Optional[uuid.UUID] = None
    group_name: Optional[str] = None
    budget_amount: Optional[Decimal] = None
    actual_amount: Decimal
    projected_amount: Decimal = Decimal("0")
    prev_month_amount: Decimal = Decimal("0")
    projected_prev_month_amount: Decimal = Decimal("0")
    percentage_used: Optional[float] = None
    is_recurring: bool = False
    end_month: Optional[_Date] = None  # Fork: the budget's last month, if it has one


# ---------------------------------------------------------------------------
# Fork: budget groups (one cap over several categories)
# ---------------------------------------------------------------------------


class BudgetGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    amount: Decimal
    month: _Date  # First day of month
    is_recurring: bool = False
    end_month: Optional[_Date] = None
    category_ids: list[uuid.UUID] = Field(min_length=1)


class BudgetGroupUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    amount: Optional[Decimal] = None
    month: Optional[_Date] = None
    is_recurring: Optional[bool] = None
    end_month: Optional[_Date] = None  # null clears it
    category_ids: Optional[list[uuid.UUID]] = Field(default=None, min_length=1)
    # Fork: a repeating group's new amount applies from this month on.
    effective_month: Optional[_Date] = None


class BudgetGroupRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    amount: Decimal
    month: _Date
    is_recurring: bool
    end_month: Optional[_Date] = None
    category_ids: list[uuid.UUID]

    model_config = ConfigDict(from_attributes=True)


class BudgetGroupVsActual(BaseModel):
    """A group's cap for a month against its member categories' spending."""

    id: uuid.UUID
    name: str
    amount: Decimal
    month: _Date
    is_recurring: bool
    end_month: Optional[_Date] = None
    category_ids: list[uuid.UUID]
    actual_amount: Decimal
    projected_amount: Decimal
    percentage_used: Optional[float] = None
