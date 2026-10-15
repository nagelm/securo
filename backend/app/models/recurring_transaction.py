import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional, cast

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.account import Account
    from app.models.category import Category
    from app.models.payee import Payee
    from app.models.transaction import Transaction
    from app.models.user import User


class RecurringTransaction(Base):
    __tablename__ = "recurring_transactions"
    __table_args__ = (
        UniqueConstraint("user_id", "description", "frequency", "start_date", name="uq_recurring_tx"),
        CheckConstraint(
            "weekend_adjustment IN ('none', 'previous_friday', 'next_monday')",
            name="ck_recurring_transactions_weekend_adjustment",
        ),
        CheckConstraint(
            "kind IN ('standard', 'loan_payment')",
            name="ck_recurring_transactions_kind",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True)
    category_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("categories.id"), nullable=True)
    description: Mapped[str] = mapped_column(String(500))
    amount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    type: Mapped[str] = mapped_column(String(10))  # debit, credit
    frequency: Mapped[str] = mapped_column(String(20))  # weekly, biweekly, monthly, quarterly, semiannual, yearly
    weekend_adjustment: Mapped[str] = mapped_column(
        String(20), default="none", server_default="none"
    )
    day_of_month: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # When True (the default), generate_pending materializes this bill's due
    # occurrences into real transactions. When False, no placeholder is written
    # and the bill is only shown as a projection until the actual charge is
    # matched to it (e.g. from bank sync). Either way, incoming real
    # transactions are linked back to the bill to avoid duplicates.
    auto_generate: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    next_occurrence: Mapped[date] = mapped_column(Date)
    amount_primary: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=15, scale=2), nullable=True)
    fx_rate_used: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=20, scale=10), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # `standard` or `loan_payment`. A loan payment pays the loan in
    # `transfer_account_id` from `account_id`: `amount` is the whole payment,
    # `interest_amount` its fixed interest share in `interest_category_id`.
    # Only the interest is ever an expense; the rest is principal, a transfer
    # between two of the user's own accounts (see dashboard_service
    # `_get_recurring_projections`).
    # Fork: a `standard` item with `transfer_account_id` is a transfer from
    # `account_id` to another of the user's own accounts (projection only).
    kind: Mapped[str] = mapped_column(String(20), default="standard", server_default="standard")
    transfer_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    interest_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=15, scale=2), nullable=True)
    interest_category_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    # Fork: the payee the rows this item generates or links are for (f012).
    payee_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payees.id", ondelete="SET NULL", name="fk_recurring_transactions_payee_id_payees"),
        nullable=True,
        index=True,
    )

    user: Mapped["User"] = relationship()
    account: Mapped[Optional["Account"]] = relationship(foreign_keys=[account_id])
    category: Mapped[Optional["Category"]] = relationship(foreign_keys=[category_id])
    # Named like Transaction's, so app.services.payee_display.apply_payee_display
    # fills the display fields below the same way for both.
    payee_entity: Mapped[Optional["Payee"]] = relationship(foreign_keys=[payee_id])
    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="recurring_transaction"
    )

    # Populated by the service from payee_entity (not DB columns).
    payee_name = cast(Optional[str], None)
    payee_logo_url = cast(Optional[str], None)
    payee_icon = cast(Optional[str], None)
