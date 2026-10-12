"""loan accounts: subtype, secured asset and provider loan facts

Revision ID: f002
Revises: 096
Create Date: 2026-10-12

A loan is an account with `type = "loan"`; its balance is negative (the
amount owed), so net worth already treats it as a liability. Three nullable
columns, all additive:

- `loan_subtype`: mortgage, vehicle, student, personal, line_of_credit,
  other. Display only.
- `secured_asset_id`: the asset the loan is secured against (one asset per
  loan, many loans per asset). Deleting the asset unlinks its loans. Column,
  foreign key and index names match upstream PR #1055 so a later upstream
  merge converges on the same objects.
- `loan_details`: provider loan facts written by sync and shown read-only.
  Never read by any P&L, budget or net-worth code.

Fork migration ids are `fNNN` so they can never collide with upstream's
numeric ids.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f002"
# Re-pointed to "f001" once the payee-logo migration lands as f001.
down_revision: Union[str, None] = "096"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("loan_subtype", sa.String(30), nullable=True))
    op.add_column(
        "accounts",
        sa.Column("secured_asset_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_accounts_secured_asset_id_assets",
        "accounts",
        "assets",
        ["secured_asset_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_accounts_secured_asset_id", "accounts", ["secured_asset_id"])
    op.add_column("accounts", sa.Column("loan_details", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("accounts", "loan_details")
    op.drop_index("ix_accounts_secured_asset_id", table_name="accounts")
    op.drop_constraint("fk_accounts_secured_asset_id_assets", "accounts", type_="foreignkey")
    op.drop_column("accounts", "secured_asset_id")
    op.drop_column("accounts", "loan_subtype")
