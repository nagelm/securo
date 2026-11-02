"""loan payments: links the rows one loan payment wrote

Revision ID: f003
Revises: f002
Create Date: 2026-10-12

A loan payment is a transfer pair (cash debit on the paying account, credit
on the loan) plus an optional interest debit on the loan. `loan_payments`
links those rows and stores no amounts: principal exists only as the paired
legs, which every P&L filter drops, and the read endpoints derive total,
interest and principal from the rows. Reports never read this table.

Deleting either leg cascades the record away (the interest row stays as an
interest expense); deleting the interest row only clears its link.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f003"
down_revision: Union[str, None] = "f002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "loan_payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("loan_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cash_transaction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("loan_transaction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("interest_transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cash_leg_created", sa.Boolean(), nullable=False),
        sa.Column("recurring_transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["loan_account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cash_transaction_id"], ["transactions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["loan_transaction_id"], ["transactions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["interest_transaction_id"], ["transactions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["recurring_transaction_id"], ["recurring_transactions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cash_transaction_id"),
        sa.UniqueConstraint("loan_transaction_id"),
        sa.UniqueConstraint("interest_transaction_id"),
    )
    op.create_index("ix_loan_payments_workspace_id", "loan_payments", ["workspace_id"])
    op.create_index("ix_loan_payments_loan_account_id", "loan_payments", ["loan_account_id"])


def downgrade() -> None:
    op.drop_index("ix_loan_payments_loan_account_id", table_name="loan_payments")
    op.drop_index("ix_loan_payments_workspace_id", table_name="loan_payments")
    op.drop_table("loan_payments")
