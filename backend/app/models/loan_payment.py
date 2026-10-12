import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LoanPayment(Base):
    """Links the rows one loan payment wrote; stores no amounts.

    A payment is a transfer pair (cash debit on the paying account, credit
    on the loan) plus an optional interest debit on the loan. Principal
    exists only as the paired legs, which every P&L filter drops, so no
    report has an amount here to sum. Read endpoints derive total, interest
    and principal from the linked rows.

    Metadata over the ledger, never a source for reports: P&L, budget and
    net-worth code must not read this table.

    Deleting either leg (the ordinary transaction UI deletes both) cascades
    the record away and leaves the interest row as a plain interest expense.
    """

    __tablename__ = "loan_payments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    loan_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    # The debit leg on the paying account.
    cash_transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="CASCADE"), unique=True
    )
    # The credit leg on the loan.
    loan_transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="CASCADE"), unique=True
    )
    # The interest debit on the loan; null when the payment had no interest.
    interest_transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    # True when the payment wrote the cash debit; false when it paired a
    # debit that already existed (synced or imported).
    cash_leg_created: Mapped[bool] = mapped_column(Boolean, default=True)
    recurring_transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recurring_transactions.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
