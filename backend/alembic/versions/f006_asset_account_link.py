"""link a synced holding to the account whose balance includes it

Revision ID: f006
Revises: f005
Create Date: 2026-10-13

`assets.account_external_id` names the synced account (by its external id on
the asset's connection) whose balance already carries the holding. Net worth
leaves such a holding out while that account is open, so an investment account
and its holdings are counted once. Sync fills the column for providers whose
investment accounts report their holdings' total (Akahu); no existing row is
changed here.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f006"
down_revision: Union[str, None] = "f005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column("account_external_id", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assets", "account_external_id")
