"""budget group caps from a month on

Revision ID: f021
Revises: f020
Create Date: 2026-10-17

Owner 2026-10-17: a new cap for a repeating budget group, set while viewing
a later month, applies from that month on, as a budget's amount does; the
months before keep theirs. A group stays one row (a category is in one
group only), so each later cap is a row of `budget_group_caps` (the group,
the first day of the month it starts, the amount; one per group and month).
`budget_groups.amount` is the cap from the group's own month until the
first of them.

Downgrade drops the table: every month falls back to `budget_groups.amount`.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f021"
down_revision: Union[str, None] = "f020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_uuid = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "budget_group_caps",
        sa.Column("id", _uuid, primary_key=True),
        sa.Column(
            "group_id", _uuid, sa.ForeignKey("budget_groups.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.UniqueConstraint("group_id", "month", name="uq_budget_group_caps_group_month"),
    )


def downgrade() -> None:
    op.drop_table("budget_group_caps")
