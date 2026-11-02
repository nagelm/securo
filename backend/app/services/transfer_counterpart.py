"""The accounts on both sides of a linked transfer, for a whole page at once.

Both legs of a transfer share one ``transfer_pair_id``; neither row points
at the other. A list that wants to show "From A → To B" needs the other
leg's account, and enough of both accounts to draw them even when the
client has not loaded them (a closed account, say). This looks all of that
up for a page in a fixed number of queries, however many rows it has.
"""

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.account import Account
from app.models.transaction import Transaction
from app.services.account_service import _institution


def _account_ref(acc: Account) -> dict:
    """What a transfer pill needs to draw an account: its name and thumbnail."""
    _, logo = _institution(acc, acc.connection)
    return {
        "id": acc.id,
        "name": acc.name,
        "display_name": acc.display_name,
        "type": acc.type,
        "institution_logo_url": logo,
        "is_closed": bool(acc.is_closed),
    }


async def attach_transfer_counterpart_accounts(
    session: AsyncSession, transactions: Iterable[Transaction]
) -> None:
    """Fill the transfer fields on every paired leg in the batch.

    Sets ``transfer_counterpart_account_id`` and, as drawable summaries,
    ``transfer_account`` (this leg's account) and
    ``transfer_counterpart_account``. Unpaired rows, and rows whose other
    leg is missing, get None for the counterpart. Legs are matched within
    their own workspace, so a pair id never reaches across.

    Queries: one for the legs, one for their accounts (institution and
    connection joined in), and at most one more that SQLAlchemy issues for
    the connections' institution list. None at all when nothing is paired.
    """
    txs = list(transactions)
    for tx in txs:
        tx.transfer_counterpart_account_id = None
        tx.transfer_account = None
        tx.transfer_counterpart_account = None
    pair_ids = {tx.transfer_pair_id for tx in txs if tx.transfer_pair_id is not None}
    if not pair_ids:
        return

    rows = await session.execute(
        select(
            Transaction.transfer_pair_id,
            Transaction.workspace_id,
            Transaction.id,
            Transaction.account_id,
        ).where(Transaction.transfer_pair_id.in_(pair_ids))
    )
    legs: dict[tuple[uuid.UUID, uuid.UUID], list[tuple[uuid.UUID, uuid.UUID]]] = {}
    account_ids: set[uuid.UUID] = set()
    for pair_id, workspace_id, tx_id, account_id in rows.all():
        legs.setdefault((pair_id, workspace_id), []).append((tx_id, account_id))
        account_ids.add(account_id)

    accounts = await session.execute(
        select(Account)
        .where(Account.id.in_(account_ids))
        .options(joinedload(Account.institution), joinedload(Account.connection))
    )
    refs = {acc.id: _account_ref(acc) for acc in accounts.unique().scalars().all()}

    for tx in txs:
        if tx.transfer_pair_id is None:
            continue
        others = [
            account_id
            for tx_id, account_id in legs.get((tx.transfer_pair_id, tx.workspace_id), [])
            if tx_id != tx.id
        ]
        tx.transfer_account = refs.get(tx.account_id) if tx.account_id else None
        if others:
            tx.transfer_counterpart_account_id = others[0]
            tx.transfer_counterpart_account = refs.get(others[0])
