"""recurring payee

Revision ID: f012
Revises: f011
Create Date: 2026-10-14

A recurring item gets a payee (`recurring_transactions.payee_id`, nullable,
SET NULL when the payee goes), so the Recurring list can show its logo and the
rows it generates or links carry it.

Existing items are backfilled, each only while it has no payee:

  1. the payee of the most recent transaction linked to the item
     (`transactions.recurring_transaction_id`) that has one, from the item's
     own workspace;
  2. otherwise a payee of the same workspace whose name matches the item's
     description, case-insensitively: the whole description ("Netflix"), or
     its start followed by a space or punctuation ("Acme" for "Acme
     insurance"). The longest matching name wins; if two payees tie, the item
     is left without one.

`payee_for_description` is the pure matching rule, shared with the tests.
Downgrade drops the column.
"""
import uuid
from typing import Iterable, Optional, Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f012"
down_revision: Union[str, None] = "f011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_uuid = postgresql.UUID(as_uuid=True)
recurring = sa.table(
    "recurring_transactions",
    sa.column("id", _uuid),
    sa.column("workspace_id", _uuid),
    sa.column("description", sa.String),
    sa.column("payee_id", _uuid),
)
transactions = sa.table(
    "transactions",
    sa.column("id", _uuid),
    sa.column("recurring_transaction_id", _uuid),
    sa.column("payee_id", _uuid),
    sa.column("date", sa.Date),
    sa.column("created_at", sa.DateTime),
)
payees = sa.table(
    "payees",
    sa.column("id", _uuid),
    sa.column("workspace_id", _uuid),
    sa.column("name", sa.String),
)


def payee_for_description(
    description: Optional[str], candidates: Iterable[tuple[uuid.UUID, str]]
) -> Optional[uuid.UUID]:
    """The payee whose name the description is, or starts with as a word.

    Case-insensitive. "Acme" matches "Acme insurance" and "Acme" but not
    "Acmeville": the name must be followed by the end of the description or a
    character that is neither a letter nor a digit. The longest matching name
    wins; when more than one payee has it, the answer is None.
    """
    text = (description or "").strip().casefold()
    if not text:
        return None
    best_length = 0
    best: list[uuid.UUID] = []
    for payee_id, name in candidates:
        key = (name or "").strip().casefold()
        if not key or not text.startswith(key):
            continue
        if len(text) > len(key) and text[len(key)].isalnum():
            continue
        if len(key) > best_length:
            best_length, best = len(key), [payee_id]
        elif len(key) == best_length:
            best.append(payee_id)
    return best[0] if len(best) == 1 else None


def backfill(bind) -> int:
    """Give each recurring item without a payee one (see the module doc).

    Returns the number of items updated. Safe to run more than once.
    """
    pending = bind.execute(
        sa.select(recurring.c.id, recurring.c.workspace_id, recurring.c.description)
        .where(recurring.c.payee_id.is_(None))
    ).all()
    if not pending:
        return 0

    # 1. The most recent linked transaction with a payee of the same workspace.
    linked: dict[uuid.UUID, uuid.UUID] = {}
    rows = bind.execute(
        sa.select(
            transactions.c.recurring_transaction_id, transactions.c.payee_id
        )
        .select_from(
            transactions.join(
                recurring, recurring.c.id == transactions.c.recurring_transaction_id
            ).join(payees, payees.c.id == transactions.c.payee_id)
        )
        .where(
            recurring.c.payee_id.is_(None),
            transactions.c.payee_id.is_not(None),
            payees.c.workspace_id == recurring.c.workspace_id,
        )
        .order_by(
            transactions.c.recurring_transaction_id,
            transactions.c.date.desc(),
            transactions.c.created_at.desc(),
            transactions.c.id.desc(),
        )
    ).all()
    for recurring_id, payee_id in rows:
        linked.setdefault(recurring_id, payee_id)

    # 2. A payee named like the description, per workspace.
    workspace_ids = {row.workspace_id for row in pending if row.id not in linked}
    by_workspace: dict[uuid.UUID, list[tuple[uuid.UUID, str]]] = {}
    if workspace_ids:
        for payee_id, workspace_id, name in bind.execute(
            sa.select(payees.c.id, payees.c.workspace_id, payees.c.name)
            .where(payees.c.workspace_id.in_(workspace_ids))
        ).all():
            by_workspace.setdefault(workspace_id, []).append((payee_id, name))

    updated = 0
    for row in pending:
        payee_id = linked.get(row.id) or payee_for_description(
            row.description, by_workspace.get(row.workspace_id, [])
        )
        if payee_id is None:
            continue
        bind.execute(
            sa.update(recurring)
            .where(recurring.c.id == row.id, recurring.c.payee_id.is_(None))
            .values(payee_id=payee_id)
        )
        updated += 1
    return updated


def upgrade() -> None:
    op.add_column(
        "recurring_transactions",
        sa.Column("payee_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_recurring_transactions_payee_id_payees",
        "recurring_transactions",
        "payees",
        ["payee_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_recurring_transactions_payee_id",
        "recurring_transactions",
        ["payee_id"],
    )
    backfill(op.get_bind())


def downgrade() -> None:
    op.drop_index("ix_recurring_transactions_payee_id", table_name="recurring_transactions")
    op.drop_constraint(
        "fk_recurring_transactions_payee_id_payees",
        "recurring_transactions",
        type_="foreignkey",
    )
    op.drop_column("recurring_transactions", "payee_id")
