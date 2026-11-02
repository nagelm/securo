"""forecast budgets come from the canonical Budgets, by choice

Revision ID: f018
Revises: f017
Create Date: 2026-10-15

The canonical Budgets (category budgets and budget groups) are the one
budgeting mechanism. The Forecast's own `budget` kind of `forecast_items`
(f016) goes: its rows are deleted and the kind check constraint is restored
without it. Instead the Forecast applies the budgets a workspace chooses
(`forecast_budget_choices`: a category budget by its category, or a budget
group; exactly one of the two per row, each at most once per workspace).

Downgrade drops the choices and lets the kind back in; the deleted budget
items do not come back.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f018"
down_revision: Union[str, None] = "f017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_KINDS = "'one_off', 'repeating', 'recurring_change', 'income_change', 'spending_change', 'loan_change'"
_uuid = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.execute("DELETE FROM forecast_items WHERE kind = 'budget'")
    op.drop_constraint("ck_forecast_items_kind", "forecast_items", type_="check")
    op.create_check_constraint("ck_forecast_items_kind", "forecast_items", f"kind IN ({_KINDS})")
    op.create_table(
        "forecast_budget_choices",
        sa.Column("id", _uuid, primary_key=True),
        sa.Column(
            "workspace_id", _uuid, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("category_id", _uuid, sa.ForeignKey("categories.id", ondelete="CASCADE"), nullable=True),
        sa.Column(
            "budget_group_id", _uuid, sa.ForeignKey("budget_groups.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "(category_id IS NULL) <> (budget_group_id IS NULL)", name="ck_forecast_budget_choices_one_target"
        ),
        sa.UniqueConstraint("workspace_id", "category_id", name="uq_forecast_budget_choices_category"),
        sa.UniqueConstraint("workspace_id", "budget_group_id", name="uq_forecast_budget_choices_group"),
    )
    op.create_index("ix_forecast_budget_choices_workspace_id", "forecast_budget_choices", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_forecast_budget_choices_workspace_id", table_name="forecast_budget_choices")
    op.drop_table("forecast_budget_choices")
    op.drop_constraint("ck_forecast_items_kind", "forecast_items", type_="check")
    op.create_check_constraint("ck_forecast_items_kind", "forecast_items", f"kind IN ({_KINDS}, 'budget')")
