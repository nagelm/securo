"""forecast scenarios and items

Revision ID: f010
Revises: f009
Create Date: 2026-10-14

The Forecast page's data. `forecast_scenarios` are named "what if" sets with
a colour, an on/off switch and an order. `forecast_items` are expected
payments (`scenario_id` NULL: real money not in the bank yet, which drops
out once `matched_transaction_id` points at the bank row) and the changes a
scenario makes: a one-off or repeating payment, a change to a recurring
payment, to income or to a category's everyday spending. Both are workspace
rows; deleting a scenario deletes its changes. Downgrade drops both tables.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f010"
down_revision: Union[str, None] = "f009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_uuid = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "forecast_scenarios",
        sa.Column("id", _uuid, primary_key=True),
        sa.Column("user_id", _uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "workspace_id", _uuid, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("color", sa.String(length=7), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_forecast_scenarios_workspace_id", "forecast_scenarios", ["workspace_id"])

    op.create_table(
        "forecast_items",
        sa.Column("id", _uuid, primary_key=True),
        sa.Column("user_id", _uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "workspace_id", _uuid, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "scenario_id", _uuid, sa.ForeignKey("forecast_scenarios.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("amount", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("type", sa.String(length=10), nullable=True),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("frequency", sa.String(length=20), nullable=True),
        sa.Column("account_id", _uuid, sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True),
        sa.Column("category_id", _uuid, sa.ForeignKey("categories.id", ondelete="SET NULL"), nullable=True),
        sa.Column(
            "recurring_transaction_id",
            _uuid,
            sa.ForeignKey("recurring_transactions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("change_mode", sa.String(length=10), nullable=True),
        sa.Column("percent", sa.Numeric(precision=7, scale=2), nullable=True),
        sa.Column(
            "matched_transaction_id",
            _uuid,
            sa.ForeignKey("transactions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('one_off', 'repeating', 'recurring_change', 'income_change', 'spending_change')",
            name="ck_forecast_items_kind",
        ),
        sa.CheckConstraint("type IS NULL OR type IN ('debit', 'credit')", name="ck_forecast_items_type"),
        sa.CheckConstraint(
            "change_mode IS NULL OR change_mode IN ('amount', 'pause', 'stop', 'percent')",
            name="ck_forecast_items_change_mode",
        ),
    )
    op.create_index("ix_forecast_items_workspace_id", "forecast_items", ["workspace_id"])
    op.create_index("ix_forecast_items_scenario_id", "forecast_items", ["scenario_id"])


def downgrade() -> None:
    op.drop_index("ix_forecast_items_scenario_id", table_name="forecast_items")
    op.drop_index("ix_forecast_items_workspace_id", table_name="forecast_items")
    op.drop_table("forecast_items")
    op.drop_index("ix_forecast_scenarios_workspace_id", table_name="forecast_scenarios")
    op.drop_table("forecast_scenarios")
