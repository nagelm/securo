"""forecast expected transfers

Revision ID: f014
Revises: f013
Create Date: 2026-10-15

An expected payment can be a transfer between own accounts: the new
`transfer_account_id` on `forecast_items` is where the money lands, while
`account_id` is where it leaves (type debit). Deleting either account
deletes the item, as `account_id` already does.

Downgrade deletes the transfer items (without the column they would read as
a plain payment out of the from-account), then drops the column (and with
it its foreign key).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f014"
down_revision: Union[str, None] = "f013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "forecast_items",
        sa.Column(
            "transfer_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.execute("DELETE FROM forecast_items WHERE transfer_account_id IS NOT NULL")
    op.drop_column("forecast_items", "transfer_account_id")
