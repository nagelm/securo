import uuid
from datetime import date as _Date
from decimal import Decimal
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

WeekendAdjustment = Literal["none", "previous_friday", "next_monday"]
RecurringFrequency = Literal["weekly", "biweekly", "monthly", "quarterly", "semiannual", "yearly"]
DayOfMonth = Annotated[int, Field(ge=1, le=31)]
RecurringKind = Literal["standard", "loan_payment"]


class RecurringTransactionCreate(BaseModel):
    description: str
    amount: Decimal
    currency: str = "USD"
    type: str  # debit, credit
    frequency: str  # weekly, biweekly, monthly, quarterly, semiannual, yearly
    weekend_adjustment: WeekendAdjustment = "none"
    day_of_month: Optional[DayOfMonth] = None
    start_date: _Date
    end_date: Optional[_Date] = None
    account_id: uuid.UUID
    category_id: Optional[uuid.UUID] = None
    payee_id: Optional[uuid.UUID] = None  # Fork: validated to the workspace
    skip_first: bool = False  # Set true when first occurrence already created as a transaction
    # Materialize occurrences; when false, wait for the real charge. Fork: a
    # plan item or a transfer defaults to false.
    auto_generate: bool = True
    # A loan payment: `account_id` pays the loan `transfer_account_id`;
    # `amount` is the whole payment and `interest_amount` its fixed interest
    # share, booked in `interest_category_id`. The service validates them.
    kind: RecurringKind = "standard"
    transfer_account_id: Optional[uuid.UUID] = None
    interest_amount: Optional[Decimal] = None
    interest_category_id: Optional[uuid.UUID] = None
    # Fork: part of the Forecast plan, not a genuinely regular payment. A
    # `standard` item may name `transfer_account_id`: a transfer from
    # `account_id` to that other own account.
    plan: bool = False


class RecurringTransactionUpdate(BaseModel):
    description: Optional[str] = None
    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    type: Optional[str] = None
    frequency: Optional[RecurringFrequency] = None
    weekend_adjustment: Optional[WeekendAdjustment] = None
    day_of_month: Optional[DayOfMonth] = None
    start_date: Optional[_Date] = None
    end_date: Optional[_Date] = None
    account_id: Optional[uuid.UUID] = None
    category_id: Optional[uuid.UUID] = None
    payee_id: Optional[uuid.UUID] = None  # Fork: null clears it
    is_active: Optional[bool] = None
    auto_generate: Optional[bool] = None
    # The kind of a schedule never changes; a loan payment's loan and
    # interest split can. Fork: so can a standard item's transfer account
    # (null makes it a plain payment again).
    transfer_account_id: Optional[uuid.UUID] = None
    interest_amount: Optional[Decimal] = None
    interest_category_id: Optional[uuid.UUID] = None
    plan: Optional[bool] = None


class RecurringTransactionRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    account_id: Optional[uuid.UUID] = None
    category_id: Optional[uuid.UUID] = None
    description: str
    amount: Decimal
    currency: str
    type: str
    frequency: str
    weekend_adjustment: WeekendAdjustment = "none"
    day_of_month: Optional[int] = None
    start_date: _Date
    end_date: Optional[_Date] = None
    is_active: bool
    auto_generate: bool = True
    next_occurrence: _Date
    amount_primary: Optional[float] = None
    fx_rate_used: Optional[float] = None
    kind: RecurringKind = "standard"
    transfer_account_id: Optional[uuid.UUID] = None
    interest_amount: Optional[Decimal] = None
    interest_category_id: Optional[uuid.UUID] = None
    # Fork: the payee and how it is drawn, as on a transaction
    # (app.services.payee_display).
    payee_id: Optional[uuid.UUID] = None
    payee_name: Optional[str] = None
    payee_logo_url: Optional[str] = None
    payee_icon: Optional[str] = None
    # Fork: a Forecast plan item, and its progress: the bank rows linked to
    # it from its start (and to its end), and how many payments it makes
    # from start to end (None when open-ended). Filled for plan items only.
    plan: bool = False
    arrived_count: Optional[int] = None
    occurrence_count: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)
