"""budget end month and budget groups

Revision ID: f017
Revises: f016
Create Date: 2026-10-15

A recurring budget can end: `budgets.end_month` (first day of its last
month, inclusive; NULL = open-ended). A budget group is one cap over several
categories (`budget_groups`, same month / is_recurring / end_month semantics
as a budget) with its members in `budget_group_categories`; a category is
in at most one group (unique `category_id`). Deleting the group or the
category deletes the membership.

Downgrade drops both tables and the column.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f017"
down_revision: Union[str, None] = "f016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_uuid = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.add_column("budgets", sa.Column("end_month", sa.Date(), nullable=True))
    op.create_table(
        "budget_groups",
        sa.Column("id", _uuid, primary_key=True),
        sa.Column("user_id", _uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "workspace_id", _uuid, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("amount", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("currency", sa.String(3), server_default="USD", nullable=True),
        sa.Column("amount_primary", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("is_recurring", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("end_month", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_budget_groups_workspace_id", "budget_groups", ["workspace_id"])
    op.create_table(
        "budget_group_categories",
        sa.Column(
            "group_id", _uuid, sa.ForeignKey("budget_groups.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "category_id", _uuid, sa.ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.UniqueConstraint("category_id", name="uq_budget_group_categories_category"),
    )


def downgrade() -> None:
    op.drop_table("budget_group_categories")
    op.drop_index("ix_budget_groups_workspace_id", table_name="budget_groups")
    op.drop_table("budget_groups")
    op.drop_column("budgets", "end_month")
