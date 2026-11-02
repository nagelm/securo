"""move to upstream's loans: drop what they replace

Revision ID: f023
Revises: 101
Create Date: 2026-11-03

Upstream grew loan accounts of its own (097-101). Kept from ours: Akahu's
`loan_details` and the secured asset link, a column both chains add.

Replaced: recorded loan payments and recurring loan payment schedules, so their
table and columns go; `loan_subtype` becomes upstream's `loan_kind`. A connected
loan's stored balance becomes the amount owed, upstream's meaning of the column.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "f023"
down_revision: Union[str, None] = "101"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("loan_payments")
    op.drop_constraint("ck_recurring_transactions_kind", "recurring_transactions", type_="check")
    op.drop_column("recurring_transactions", "interest_category_id")
    op.drop_column("recurring_transactions", "interest_amount")
    op.drop_column("recurring_transactions", "kind")
    op.execute(
        """
        UPDATE accounts SET loan_kind = CASE loan_subtype
            WHEN 'vehicle' THEN 'auto'
            WHEN 'line_of_credit' THEN 'other'
            ELSE loan_subtype END
        WHERE loan_subtype IS NOT NULL AND loan_kind IS NULL
        """
    )
    op.drop_column("accounts", "loan_subtype")
    op.execute(
        "UPDATE accounts SET balance = abs(balance) WHERE type = 'loan' AND connection_id IS NOT NULL"
    )


def downgrade() -> None:
    raise NotImplementedError("Restore from the backup taken before this migration")
