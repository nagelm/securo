"""one kind of recurring item: drop recurring_transactions.plan

Revision ID: f020
Revises: f019
Create Date: 2026-10-15

Owner 2026-10-15: every recurring item is the same object, listed and
edited on the Recurring page and shown on the Forecast page's expected
transactions like any other recurring payment. The `plan` flag (f019) goes;
the items it marked stay as ordinary recurring items with their own
auto_generate, transfer account, links and dates.

Downgrade re-adds the column with default false: every item comes back as
an ordinary recurring item (which ones were plan items is not kept).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f020"
down_revision: Union[str, None] = "f019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("recurring_transactions", "plan")


def downgrade() -> None:
    op.add_column(
        "recurring_transactions",
        sa.Column("plan", sa.Boolean(), server_default="false", nullable=False),
    )
