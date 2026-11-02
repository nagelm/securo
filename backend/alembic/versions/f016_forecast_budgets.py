"""forecast plan budgets

Revision ID: f016
Revises: f015
Create Date: 2026-10-15

A `forecast_items` row of the new kind `budget` (no scenario) is a plan for
everyday spending: an amount per weekly, fortnightly or monthly period
between a start and an end date, optionally out of one account and for one
category. The forecast projects it on that schedule and switches the
history-based spending estimate off over its dates. Only the kind check
constraint changes; the columns already exist.

Downgrade deletes every budget (the old constraint refuses them), then
restores the constraint.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "f016"
down_revision: Union[str, None] = "f015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_KINDS = "'one_off', 'repeating', 'recurring_change', 'income_change', 'spending_change', 'loan_change'"


def upgrade() -> None:
    op.drop_constraint("ck_forecast_items_kind", "forecast_items", type_="check")
    op.create_check_constraint("ck_forecast_items_kind", "forecast_items", f"kind IN ({_OLD_KINDS}, 'budget')")


def downgrade() -> None:
    op.execute("DELETE FROM forecast_items WHERE kind = 'budget'")
    op.drop_constraint("ck_forecast_items_kind", "forecast_items", type_="check")
    op.create_check_constraint("ck_forecast_items_kind", "forecast_items", f"kind IN ({_OLD_KINDS})")
