"""Fork: the Forecast page's own data.

`ForecastScenario` is a named "what if" set; `ForecastItem` is either an
expected payment (``scenario_id`` NULL, kind ``one_off``: real money that is
not in the bank yet; with ``transfer_account_id`` a transfer between own
accounts) or one change inside a scenario (including a change to a loan's
rate or repayments, ``loan_change``). Both are read by `forecast_service`,
which lays them over the cash-flow projection.

Fork: repeating expected payments are ordinary recurring items (f019, f020),
not forecast items.
"""
import uuid
from datetime import date as _date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base

FORECAST_ITEM_KINDS = (
    "one_off", "repeating", "recurring_change", "income_change", "spending_change", "loan_change",
)
FORECAST_CHANGE_MODES = ("amount", "pause", "stop", "percent", "rate", "lump_sum")


class ForecastScenario(Base):
    __tablename__ = "forecast_scenarios"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    color: Mapped[str] = mapped_column(String(7), default="#F59E0B")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    items: Mapped[list["ForecastItem"]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan"
    )


class ForecastItem(Base):
    __tablename__ = "forecast_items"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('one_off', 'repeating', 'recurring_change', 'income_change', 'spending_change',"
            " 'loan_change')",
            name="ck_forecast_items_kind",
        ),
        CheckConstraint(
            "type IS NULL OR type IN ('debit', 'credit')",
            name="ck_forecast_items_type",
        ),
        CheckConstraint(
            "change_mode IS NULL OR change_mode IN ('amount', 'pause', 'stop', 'percent', 'rate', 'lump_sum')",
            name="ck_forecast_items_change_mode",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    # NULL: an expected payment, part of the base forecast.
    scenario_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("forecast_scenarios.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(String(500), default="", server_default="")
    # Positive for one_off/repeating/recurring_change (direction in `type`);
    # a signed change for income_change (per occurrence) and spending_change
    # (per month), where a negative amount is a cut.
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=15, scale=2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    type: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # debit, credit
    # one_off: the day it lands; changes: the day they take effect.
    date: Mapped[Optional[_date]] = mapped_column(Date, nullable=True)
    start_date: Mapped[Optional[_date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[_date]] = mapped_column(Date, nullable=True)
    frequency: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True
    )
    # Fork: an expected transfer between own accounts: money leaves
    # `account_id` (type debit) and lands here on the same date.
    transfer_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True
    )
    category_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    recurring_transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recurring_transactions.id", ondelete="CASCADE"), nullable=True
    )
    # recurring_change: amount | pause | stop; income/spending_change: amount | percent;
    # loan_change (account_id = the loan): rate (new rate in `percent`, and optionally a
    # new repayment in `amount`) | amount (new
    # repayment) | lump_sum (one extra repayment of `amount` on start_date).
    change_mode: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    percent: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=7, scale=2), nullable=True)
    # loan_change rate: True recalculates the repayment over the remaining term
    # (a refix), False keeps it so the term changes. NULL elsewhere.
    recalculate_repayment: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    # Expected payments: the bank row that turned out to be this payment.
    matched_transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    scenario: Mapped[Optional[ForecastScenario]] = relationship(back_populates="items")


class ForecastBudgetChoice(Base):
    """Fork: a budget the Forecast applies to its spending estimate (f018).

    Opt-in per workspace: one row per chosen category budget (by
    ``category_id``, so it follows the budgets' month-to-month resolution)
    or chosen budget group (``budget_group_id``). Unchosen budgets have no
    effect on the forecast."""

    __tablename__ = "forecast_budget_choices"
    __table_args__ = (
        CheckConstraint(
            "(category_id IS NULL) <> (budget_group_id IS NULL)",
            name="ck_forecast_budget_choices_one_target",
        ),
        UniqueConstraint("workspace_id", "category_id", name="uq_forecast_budget_choices_category"),
        UniqueConstraint("workspace_id", "budget_group_id", name="uq_forecast_budget_choices_group"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    category_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id", ondelete="CASCADE"), nullable=True
    )
    budget_group_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("budget_groups.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
