"""user-defined account order

Revision ID: f008
Revises: f007
Create Date: 2026-10-13

`accounts.position` is where an account sits in its workspace's account
lists, smallest first. Existing accounts are numbered 0, 1, 2... within each
workspace in alphabetical order of the name the user sees (display name, else
the bank's name), so every list keeps looking the way it did until somebody
reorders. Display only: nothing that computes a balance reads it.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f008"
down_revision: Union[str, None] = "f007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
    )
    # The same key the API lists by (app.services.account_order), so the
    # numbering reproduces today's order. A correlated count rather than a
    # window function, so it reads the same on PostgreSQL and SQLite.
    op.execute(
        """
        UPDATE accounts SET position = (
            SELECT COUNT(*) FROM accounts AS other
            WHERE other.workspace_id = accounts.workspace_id
              AND (
                LOWER(COALESCE(other.display_name, other.name))
                    < LOWER(COALESCE(accounts.display_name, accounts.name))
                OR (
                    LOWER(COALESCE(other.display_name, other.name))
                        = LOWER(COALESCE(accounts.display_name, accounts.name))
                    AND other.name < accounts.name
                )
                OR (
                    LOWER(COALESCE(other.display_name, other.name))
                        = LOWER(COALESCE(accounts.display_name, accounts.name))
                    AND other.name = accounts.name
                    AND CAST(other.id AS VARCHAR(36)) < CAST(accounts.id AS VARCHAR(36))
                )
              )
        )
        """
    )


def downgrade() -> None:
    op.drop_column("accounts", "position")
