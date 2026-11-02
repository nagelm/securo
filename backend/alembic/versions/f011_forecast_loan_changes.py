"""forecast loan changes

Revision ID: f011
Revises: f010
Create Date: 2026-10-14

A scenario change of kind `loan_change`: on a loan account, from a date, a new
interest rate (`change_mode` 'rate', the rate in `percent`), a new repayment
amount ('amount') or a one-off extra repayment ('lump_sum'). The new boolean
`recalculate_repayment` says whether a new rate recalculates the repayment
over the remaining term (a refix) or keeps it, so the term changes. The kind
and change-mode check constraints are widened to match.

Downgrade deletes every `loan_change` item (the old constraints refuse them),
then restores the constraints and drops the column.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f011"
down_revision: Union[str, None] = "f010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_KINDS = "'one_off', 'repeating', 'recurring_change', 'income_change', 'spending_change'"
_OLD_MODES = "'amount', 'pause', 'stop', 'percent'"


def upgrade() -> None:
    op.add_column("forecast_items", sa.Column("recalculate_repayment", sa.Boolean(), nullable=True))
    op.drop_constraint("ck_forecast_items_kind", "forecast_items", type_="check")
    op.create_check_constraint(
        "ck_forecast_items_kind", "forecast_items", f"kind IN ({_OLD_KINDS}, 'loan_change')"
    )
    op.drop_constraint("ck_forecast_items_change_mode", "forecast_items", type_="check")
    op.create_check_constraint(
        "ck_forecast_items_change_mode",
        "forecast_items",
        f"change_mode IS NULL OR change_mode IN ({_OLD_MODES}, 'rate', 'lump_sum')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM forecast_items WHERE kind = 'loan_change'")
    op.drop_constraint("ck_forecast_items_change_mode", "forecast_items", type_="check")
    op.create_check_constraint(
        "ck_forecast_items_change_mode", "forecast_items", f"change_mode IS NULL OR change_mode IN ({_OLD_MODES})"
    )
    op.drop_constraint("ck_forecast_items_kind", "forecast_items", type_="check")
    op.create_check_constraint("ck_forecast_items_kind", "forecast_items", f"kind IN ({_OLD_KINDS})")
    op.drop_column("forecast_items", "recalculate_repayment")
