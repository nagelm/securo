"""recurring plan items replace repeating expected payments

Revision ID: f019
Revises: f018
Create Date: 2026-10-15

The Forecast plan's repeating payments become recurring items:
`recurring_transactions.plan` (default false) marks an item that is part of
the Forecast plan rather than a genuinely regular payment. A `standard`
recurring item may now carry `transfer_account_id` (a transfer between own
accounts); no column changes for that.

Every repeating expected payment (`forecast_items` of kind `repeating` with
no scenario) becomes a plan recurring item: same amount, type, currency,
frequency, start and end, account, transfer account, category and
description; projection only (auto_generate false); next_occurrence the
first occurrence after today; inactive when that is past its end. A name
already taken by a recurring item of the same frequency and start gets
" (plan)" appended (uq_recurring_tx). Each bank row matched to one of its
occurrences (`forecast_item_matches`) is linked to the new item through
`transactions.recurring_transaction_id`, unless another recurring item
already claims it. The forecast items are deleted and `forecast_item_matches`
is dropped. Scenario items of kind `repeating` are untouched.

Downgrade (best effort): recreates `forecast_item_matches`, turns each plan
item back into a repeating expected payment, with one match per linked bank
row (dated by the row, one per date) and those rows unlinked, deletes the
plan items and drops the column. Plain (not plan) recurring transfers stay.
"""
import calendar
import uuid
from datetime import date, timedelta
from typing import Optional, Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f019"
down_revision: Union[str, None] = "f018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_uuid = postgresql.UUID(as_uuid=True)


def _advance(current: date, frequency: str, day: int) -> date:
    """The next occurrence, as recurring_transaction_service._advance_date."""
    if frequency == "weekly":
        return current + timedelta(weeks=1)
    if frequency == "biweekly":
        return current + timedelta(weeks=2)
    if frequency == "yearly":
        year = current.year + 1
        return date(year, current.month, min(day, calendar.monthrange(year, current.month)[1]))
    months = {"quarterly": 3, "semiannual": 6}.get(frequency, 1)
    index = current.month - 1 + months
    year, month = current.year + index // 12, index % 12 + 1
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _first_after(start: date, frequency: str, today: date) -> date:
    current = start
    while current <= today:
        current = _advance(current, frequency, start.day)
    return current


def upgrade() -> None:
    op.add_column(
        "recurring_transactions",
        sa.Column("plan", sa.Boolean(), server_default="false", nullable=False),
    )
    bind = op.get_bind()
    today = date.today()
    items = bind.execute(sa.text(
        "SELECT id, user_id, workspace_id, description, amount, currency, type, frequency, start_date, "
        "end_date, account_id, transfer_account_id, category_id FROM forecast_items "
        "WHERE kind = 'repeating' AND scenario_id IS NULL "
        "AND start_date IS NOT NULL AND frequency IS NOT NULL"
    )).mappings().all()
    for item in items:
        description = item["description"] or "Expected payment"
        name, n = description, 1
        while bind.execute(sa.text(
            "SELECT 1 FROM recurring_transactions WHERE user_id = :u AND description = :d "
            "AND frequency = :f AND start_date = :s"
        ), {"u": item["user_id"], "d": name, "f": item["frequency"], "s": item["start_date"]}).first():
            name = f"{description} (plan)" if n == 1 else f"{description} (plan {n})"
            n += 1
        next_occurrence = _first_after(item["start_date"], item["frequency"], today)
        end: Optional[date] = item["end_date"]
        rec_id = uuid.uuid4()
        bind.execute(sa.text(
            "INSERT INTO recurring_transactions (id, user_id, workspace_id, account_id, category_id, "
            "description, amount, currency, type, frequency, weekend_adjustment, start_date, end_date, "
            "is_active, auto_generate, next_occurrence, kind, transfer_account_id, plan) VALUES "
            "(:id, :user_id, :workspace_id, :account_id, :category_id, :description, :amount, :currency, "
            ":type, :frequency, 'none', :start_date, :end_date, :is_active, false, :next_occurrence, "
            "'standard', :transfer_account_id, true)"
        ), {
            "id": rec_id, "user_id": item["user_id"], "workspace_id": item["workspace_id"],
            "account_id": item["account_id"], "category_id": item["category_id"], "description": name,
            "amount": item["amount"], "currency": item["currency"], "type": item["type"] or "debit",
            "frequency": item["frequency"], "start_date": item["start_date"], "end_date": end,
            "is_active": end is None or next_occurrence <= end, "next_occurrence": next_occurrence,
            "transfer_account_id": item["transfer_account_id"],
        })
        bind.execute(sa.text(
            "UPDATE transactions SET recurring_transaction_id = :rec WHERE recurring_transaction_id IS NULL "
            "AND id IN (SELECT transaction_id FROM forecast_item_matches WHERE item_id = :item)"
        ), {"rec": rec_id, "item": item["id"]})
        bind.execute(sa.text("DELETE FROM forecast_items WHERE id = :id"), {"id": item["id"]})
    op.drop_index("ix_forecast_item_matches_transaction_id", table_name="forecast_item_matches")
    op.drop_index("ix_forecast_item_matches_item_id", table_name="forecast_item_matches")
    op.drop_table("forecast_item_matches")


def downgrade() -> None:
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
    bind = op.get_bind()
    plans = bind.execute(sa.text(
        "SELECT id, user_id, workspace_id, description, amount, currency, type, frequency, start_date, "
        "end_date, account_id, transfer_account_id, category_id FROM recurring_transactions WHERE plan"
    )).mappings().all()
    for rec in plans:
        item_id = uuid.uuid4()
        bind.execute(sa.text(
            "INSERT INTO forecast_items (id, user_id, workspace_id, kind, description, amount, currency, type, "
            "start_date, end_date, frequency, account_id, transfer_account_id, category_id) VALUES "
            "(:id, :user_id, :workspace_id, 'repeating', :description, :amount, :currency, :type, "
            ":start_date, :end_date, :frequency, :account_id, :transfer_account_id, :category_id)"
        ), {
            "id": item_id, **{k: rec[k] for k in (
                "user_id", "workspace_id", "description", "amount", "currency", "type", "start_date",
                "end_date", "frequency", "account_id", "transfer_account_id", "category_id",
            )},
        })
        seen: set[date] = set()
        for tx_id, when in bind.execute(sa.text(
            "SELECT id, date FROM transactions WHERE recurring_transaction_id = :rec ORDER BY date"
        ), {"rec": rec["id"]}).all():
            if when not in seen:
                seen.add(when)
                bind.execute(sa.text(
                    "INSERT INTO forecast_item_matches (id, item_id, occurrence_date, transaction_id) "
                    "VALUES (:id, :item, :when, :tx)"
                ), {"id": uuid.uuid4(), "item": item_id, "when": when, "tx": tx_id})
        bind.execute(sa.text(
            "UPDATE transactions SET recurring_transaction_id = NULL WHERE recurring_transaction_id = :rec"
        ), {"rec": rec["id"]})
        bind.execute(sa.text("DELETE FROM recurring_transactions WHERE id = :id"), {"id": rec["id"]})
    op.drop_column("recurring_transactions", "plan")
