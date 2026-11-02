"""goals: periodic contribution targets and ongoing savings goals

Revision ID: f007
Revises: f006
Create Date: 2026-10-13

A goal can carry a periodic contribution target: save `contribution_amount`
every `contribution_period` (`weekly`, `biweekly` or `monthly`, the
recurring-transaction words). Both are set together or not at all.

`target_amount` becomes nullable so a goal can be an ongoing savings goal (a
sinking fund) with only a periodic target. Every goal must keep one of the
two. Existing goals all have a `target_amount` and no periodic target, so no
row changes.

The downgrade drops the periodic target. An ongoing goal has no total to fall
back on, so it gets a `target_amount` of 0 before the column turns NOT NULL
again; nothing else about it changes.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f007"
down_revision: Union[str, None] = "f006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("goals") as batch:
        batch.add_column(sa.Column("contribution_amount", sa.Numeric(15, 2), nullable=True))
        batch.add_column(sa.Column("contribution_period", sa.String(20), nullable=True))
        batch.alter_column("target_amount", existing_type=sa.Numeric(15, 2), nullable=True)
        batch.create_check_constraint(
            "ck_goals_contribution_period",
            "contribution_period IS NULL OR contribution_period IN ('weekly', 'biweekly', 'monthly')",
        )
        batch.create_check_constraint(
            "ck_goals_contribution_pair",
            "(contribution_amount IS NULL) = (contribution_period IS NULL)",
        )
        batch.create_check_constraint(
            "ck_goals_has_target",
            "target_amount IS NOT NULL OR contribution_amount IS NOT NULL",
        )


def downgrade() -> None:
    op.execute("UPDATE goals SET target_amount = 0 WHERE target_amount IS NULL")
    with op.batch_alter_table("goals") as batch:
        batch.drop_constraint("ck_goals_has_target", type_="check")
        batch.drop_constraint("ck_goals_contribution_pair", type_="check")
        batch.drop_constraint("ck_goals_contribution_period", type_="check")
        batch.alter_column("target_amount", existing_type=sa.Numeric(15, 2), nullable=False)
        batch.drop_column("contribution_period")
        batch.drop_column("contribution_amount")
