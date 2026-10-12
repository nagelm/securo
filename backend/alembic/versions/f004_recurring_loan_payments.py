"""recurring loan payments: a schedule that pays a loan

Revision ID: f004
Revises: f003
Create Date: 2026-10-13

A recurring transaction gains a `kind`. `standard` is every existing row.
`loan_payment` pays a loan on a schedule: `account_id` is the paying account,
`amount` the whole payment, `transfer_account_id` the loan, and
`interest_amount` / `interest_category_id` the fixed interest share of every
occurrence. Only the interest is ever projected as an expense; the rest is
principal, which moves money between two of the user's own accounts.

Deleting the loan deletes its schedules (`ON DELETE CASCADE`, like
`account_id`). Deleting the interest category only clears the link.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f004"
down_revision: Union[str, None] = "f003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "recurring_transactions",
        sa.Column("kind", sa.String(20), nullable=False, server_default="standard"),
    )
    op.create_check_constraint(
        "ck_recurring_transactions_kind",
        "recurring_transactions",
        "kind IN ('standard', 'loan_payment')",
    )
    op.add_column(
        "recurring_transactions",
        sa.Column("transfer_account_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_recurring_transactions_transfer_account_id_accounts",
        "recurring_transactions",
        "accounts",
        ["transfer_account_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_recurring_transactions_transfer_account_id",
        "recurring_transactions",
        ["transfer_account_id"],
    )
    op.add_column(
        "recurring_transactions",
        sa.Column("interest_amount", sa.Numeric(15, 2), nullable=True),
    )
    op.add_column(
        "recurring_transactions",
        sa.Column("interest_category_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_recurring_transactions_interest_category_id_categories",
        "recurring_transactions",
        "categories",
        ["interest_category_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_recurring_transactions_interest_category_id_categories",
        "recurring_transactions",
        type_="foreignkey",
    )
    op.drop_column("recurring_transactions", "interest_category_id")
    op.drop_column("recurring_transactions", "interest_amount")
    op.drop_index(
        "ix_recurring_transactions_transfer_account_id", table_name="recurring_transactions"
    )
    op.drop_constraint(
        "fk_recurring_transactions_transfer_account_id_accounts",
        "recurring_transactions",
        type_="foreignkey",
    )
    op.drop_column("recurring_transactions", "transfer_account_id")
    op.drop_constraint(
        "ck_recurring_transactions_kind", "recurring_transactions", type_="check"
    )
    op.drop_column("recurring_transactions", "kind")
