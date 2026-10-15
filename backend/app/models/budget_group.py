"""Fork: a budget group, one cap over several categories (f017).

The cap limits the group's total without fixing how much each member
category contributes. Month semantics as a category budget: ``month`` is
the first day of a month; ``is_recurring`` true applies from ``month``
through ``end_month`` (inclusive, NULL = open-ended), false applies to
``month`` only. A category is in at most one group (unique ``category_id``
in ``budget_group_categories``; categories belong to one workspace).
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base


class BudgetGroup(Base):
    __tablename__ = "budget_groups"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    amount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2))
    currency: Mapped[Optional[str]] = mapped_column(String(3), server_default="USD", nullable=True)
    amount_primary: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=15, scale=2), nullable=True)
    month: Mapped[date] = mapped_column(Date)  # First day of month
    is_recurring: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    end_month: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    members: Mapped[list["BudgetGroupCategory"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, lazy="selectin"
    )

    @property
    def category_ids(self) -> list[uuid.UUID]:
        return [m.category_id for m in self.members]

    def applies_to(self, month_start: date) -> bool:
        """Whether the cap holds in the month starting ``month_start``."""
        if not self.is_recurring:
            return self.month == month_start
        return self.month <= month_start and (self.end_month is None or month_start <= self.end_month)


class BudgetGroupCategory(Base):
    __tablename__ = "budget_group_categories"
    # A category is in at most one group.
    __table_args__ = (UniqueConstraint("category_id", name="uq_budget_group_categories_category"),)

    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("budget_groups.id", ondelete="CASCADE"), primary_key=True
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True
    )
