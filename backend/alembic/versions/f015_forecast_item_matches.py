"""forecast item matches

Revision ID: f015
Revises: f014
Create Date: 2026-10-15

An expected payment (no scenario) can now be of kind `repeating`, and each
of its occurrences is matched to a bank row on its own. The new table
`forecast_item_matches` holds one row per matched occurrence: the item, the
occurrence's date (unique per item) and the bank row. Deleting the item or
the bank row deletes the match, so the occurrence is expected again.

Downgrade drops the table. Repeating expected payments stay; the code
before this revision ignores them.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f015"
down_revision: Union[str, None] = "f014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_uuid = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "forecast_item_matches",
        sa.Column("id", _uuid, primary_key=True),
        sa.Column("item_id", _uuid, sa.ForeignKey("forecast_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("occurrence_date", sa.Date(), nullable=False),
        sa.Column(
            "transaction_id", _uuid, sa.ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("item_id", "occurrence_date", name="uq_forecast_item_matches_occurrence"),
    )
    op.create_index("ix_forecast_item_matches_item_id", "forecast_item_matches", ["item_id"])
    op.create_index("ix_forecast_item_matches_transaction_id", "forecast_item_matches", ["transaction_id"])


def downgrade() -> None:
    op.drop_index("ix_forecast_item_matches_transaction_id", table_name="forecast_item_matches")
    op.drop_index("ix_forecast_item_matches_item_id", table_name="forecast_item_matches")
    op.drop_table("forecast_item_matches")
