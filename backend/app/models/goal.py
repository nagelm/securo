import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import JSON

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.account import Account
    from app.models.asset import Asset
    from app.models.asset_group import AssetGroup
    from app.models.user import User


#: How often a goal's periodic contribution target recurs. The words are the
#: recurring-transaction frequencies (`biweekly` is a fortnight), so a
#: standing payment's frequency can be offered as a goal's period unchanged.
CONTRIBUTION_PERIODS = ("weekly", "biweekly", "monthly")


class Goal(Base):
    __tablename__ = "goals"
    __table_args__ = (
        CheckConstraint(
            "contribution_period IS NULL OR contribution_period IN ('weekly', 'biweekly', 'monthly')",
            name="ck_goals_contribution_period",
        ),
        CheckConstraint(
            "(contribution_amount IS NULL) = (contribution_period IS NULL)",
            name="ck_goals_contribution_pair",
        ),
        # A goal needs something to aim at: a total, a periodic target, or both.
        CheckConstraint(
            "target_amount IS NOT NULL OR contribution_amount IS NOT NULL",
            name="ck_goals_has_target",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    # Null for an ongoing savings goal (a sinking fund) that only has a
    # periodic contribution target.
    target_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=15, scale=2), nullable=True)
    current_amount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2), default=Decimal("0.00"))
    initial_amount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2), default=Decimal("0.00"))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    target_amount_primary: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=15, scale=2), nullable=True)
    current_amount_primary: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=15, scale=2), nullable=True)
    target_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    tracking_type: Mapped[str] = mapped_column(String(20), default="manual")  # manual, account, asset, asset_group, net_worth
    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    asset_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("assets.id"), nullable=True)
    asset_group_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("asset_groups.id", ondelete="SET NULL"), nullable=True)
    # Optional periodic target: save `contribution_amount` every
    # `contribution_period`. Display-only, like the rest of a goal.
    contribution_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=15, scale=2), nullable=True)
    contribution_period: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active, completed, paused, archived
    icon: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    color: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship()
    account: Mapped[Optional["Account"]] = relationship()
    asset: Mapped[Optional["Asset"]] = relationship()
    asset_group: Mapped[Optional["AssetGroup"]] = relationship()
