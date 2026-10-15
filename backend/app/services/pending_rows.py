"""Pending rows a provider rebuilds on every read (fork; Akahu).

Akahu gives pending transactions no id and lets their date and text change
until they settle, and says to delete and re-create them on every read
(developers.akahu.nz, "Accessing transactional data"). Sync stores each one
under an id made from its content, keeps it while the provider still lists it,
and retires it once it is gone. The settled row arrives separately with its
own id; whatever the user changed on the pending row (category, payee,
wording, notes, flags, attachments, splits, a forecast match) moves to it.

What sync itself set on a pending row is recorded in its raw data when the row
is written, so a later difference is known to be the user's edit.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any, Iterable, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.forecast import ForecastItem, ForecastItemMatch
from app.models.transaction import Transaction
from app.models.transaction_attachment import TransactionAttachment
from app.models.transaction_split import TransactionSplit

DEFAULTS_KEY = "_securo_pending_defaults"
PENDING_ID_PREFIX = "pending:"

# Fields a user may change on a pending row that should follow it to the
# settled row.
CARRIED_FIELDS = ("category_id", "payee_id", "description", "notes", "is_ignored", "exclude_from_pnl")

# How far after a pending row's date its settled row may land (cards settle
# a few days late; a weekend or holiday adds more) and how far before.
SETTLE_DAYS_AFTER = 10
SETTLE_DAYS_BEFORE = 2

# A pending row the user edited but whose settled row has not shown up yet is
# kept this long after its date, so the edit is not lost if the provider drops
# it from the pending list a sync before the settled row appears.
EDITED_GRACE_DAYS = 7


def _snapshot(tx: Transaction) -> dict[str, Any]:
    return {f: _plain(getattr(tx, f)) for f in CARRIED_FIELDS}


def _plain(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def remember_pending_defaults(tx: Transaction) -> None:
    """Record what sync set on a new pending row, before the user sees it."""
    raw = dict(tx.raw_data) if isinstance(tx.raw_data, dict) else {}
    raw[DEFAULTS_KEY] = _snapshot(tx)
    tx.raw_data = raw


def user_edits(tx: Transaction) -> dict[str, Any]:
    """The carried fields the user changed since sync wrote the row."""
    raw = tx.raw_data if isinstance(tx.raw_data, dict) else {}
    defaults = raw.get(DEFAULTS_KEY)
    if not isinstance(defaults, dict):
        return {}
    return {
        f: getattr(tx, f)
        for f in CARRIED_FIELDS
        if _plain(getattr(tx, f)) != defaults.get(f)
    }


async def _settled_match(
    session: AsyncSession,
    stale: Transaction,
    claimed: set[uuid.UUID],
) -> Optional[Transaction]:
    """The posted row that this pending row became: same account, type and
    amount, a few days either side, closest date first."""
    rows = (
        await session.execute(
            select(Transaction).where(
                Transaction.account_id == stale.account_id,
                Transaction.source == "sync",
                Transaction.status == "posted",
                Transaction.type == stale.type,
                Transaction.amount == stale.amount,
                Transaction.date >= stale.date - timedelta(days=SETTLE_DAYS_BEFORE),
                Transaction.date <= stale.date + timedelta(days=SETTLE_DAYS_AFTER),
                # The settled copy is always read after its pending row was.
                Transaction.created_at >= stale.created_at,
            )
        )
    ).scalars().all()
    candidates = [r for r in rows if r.id not in claimed]
    if not candidates:
        return None
    candidates.sort(key=lambda r: abs((r.date - stale.date).days))
    return candidates[0]


async def _hand_over(
    session: AsyncSession,
    stale: Transaction,
    settled: Transaction,
    edits: dict[str, Any],
) -> None:
    for field, value in edits.items():
        setattr(settled, field, value)
    await session.execute(
        update(TransactionAttachment)
        .where(TransactionAttachment.transaction_id == stale.id)
        .values(transaction_id=settled.id)
    )
    has_splits = (
        await session.execute(
            select(TransactionSplit.id).where(TransactionSplit.transaction_id == settled.id).limit(1)
        )
    ).first()
    if not has_splits:
        await session.execute(
            update(TransactionSplit)
            .where(TransactionSplit.transaction_id == stale.id)
            .values(transaction_id=settled.id)
        )
    await session.execute(
        update(ForecastItem)
        .where(ForecastItem.matched_transaction_id == stale.id)
        .values(matched_transaction_id=settled.id)
    )
    # Fork: and a repeating expected payment's matched occurrence.
    await session.execute(
        update(ForecastItemMatch)
        .where(ForecastItemMatch.transaction_id == stale.id)
        .values(transaction_id=settled.id)
    )


async def _unpair(session: AsyncSession, stale: Transaction) -> None:
    """A pending leg of a transfer is going: free its partner to pair again."""
    if stale.transfer_pair_id is None:
        return
    partners = (
        await session.execute(
            select(Transaction).where(
                Transaction.transfer_pair_id == stale.transfer_pair_id,
                Transaction.id != stale.id,
            )
        )
    ).scalars().all()
    for partner in partners:
        partner.transfer_pair_id = None
    stale.transfer_pair_id = None


async def retire_stale_pending(
    session: AsyncSession,
    *,
    account: Account,
    still_pending: Iterable[str],
    new_tx_ids: list[uuid.UUID],
    today: Optional[date] = None,
) -> int:
    """Delete the account's rebuilt pending rows the provider no longer lists.

    Each one's user edits go to its settled row when that row is here. An
    edited row whose settled row has not arrived yet waits (up to
    EDITED_GRACE_DAYS after its date). Rows freed from a transfer, and the
    settled rows that took one over, are added to ``new_tx_ids`` so transfer
    detection looks at them again. Returns the number of rows deleted.
    """
    today = today or app_today()
    listed = set(still_pending)
    stale_rows = (
        await session.execute(
            select(Transaction)
            .where(
                Transaction.account_id == account.id,
                Transaction.source == "sync",
                Transaction.status == "pending",
                Transaction.external_id.startswith(PENDING_ID_PREFIX),
            )
            .order_by(Transaction.date, Transaction.created_at)
        )
    ).scalars().all()
    claimed: set[uuid.UUID] = set()
    deleted = 0
    for stale in stale_rows:
        if stale.external_id in listed:
            continue
        edits = user_edits(stale)
        settled = await _settled_match(session, stale, claimed)
        if settled is None and edits and stale.date + timedelta(days=EDITED_GRACE_DAYS) >= today:
            continue
        if settled is not None:
            claimed.add(settled.id)
            await _hand_over(session, stale, settled, edits)
        had_pair = stale.transfer_pair_id is not None
        if had_pair:
            partner_ids = (
                await session.execute(
                    select(Transaction.id).where(
                        Transaction.transfer_pair_id == stale.transfer_pair_id,
                        Transaction.id != stale.id,
                    )
                )
            ).scalars().all()
            await _unpair(session, stale)
            new_tx_ids.extend(i for i in partner_ids if i not in new_tx_ids)
            if settled is not None and settled.id not in new_tx_ids:
                new_tx_ids.append(settled.id)
        if stale.id in new_tx_ids:
            new_tx_ids.remove(stale.id)
        await session.delete(stale)
        deleted += 1
    if deleted:
        await session.flush()
    return deleted
