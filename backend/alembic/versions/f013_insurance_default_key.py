"""key existing Insurance categories as the new insurance default

Revision ID: f013
Revises: f012
Create Date: 2026-10-15

Insurance became a default category (key "insurance"). A workspace that
already has a category carrying one of its translated names gets that
category keyed, as f009 did for the first defaults: per workspace, the first
such unkeyed category by id. Workspaces without one get the category when the
NZ rule pack installs, or not at all. Downgrade clears the key.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f013"
down_revision: Union[str, None] = "f012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

KEY = "insurance"
# Snapshot of the key's translated names at this revision.
NAMES: dict[str, tuple[str, ...]] = {
    KEY: ("Insurance", "Seguros", "Versicherungen", "Assurances", "Verzekeringen", "Poistenie"),
}

_uuid = postgresql.UUID(as_uuid=True)
categories = sa.table(
    "categories",
    sa.column("id", _uuid),
    sa.column("workspace_id", _uuid),
    sa.column("name", sa.String),
    sa.column("default_key", sa.String),
)


def upgrade() -> None:
    bind = op.get_bind()
    keyed = {
        row.workspace_id
        for row in bind.execute(sa.select(categories.c.workspace_id).where(categories.c.default_key == KEY))
    }
    for row in bind.execute(
        sa.select(categories.c.id, categories.c.workspace_id)
        .where(categories.c.name.in_(NAMES[KEY]), categories.c.default_key.is_(None))
        .order_by(categories.c.id)
    ):
        if row.workspace_id in keyed:
            continue
        keyed.add(row.workspace_id)
        bind.execute(categories.update().where(categories.c.id == row.id).values(default_key=KEY))


def downgrade() -> None:
    op.execute(categories.update().where(categories.c.default_key == KEY).values(default_key=None))
