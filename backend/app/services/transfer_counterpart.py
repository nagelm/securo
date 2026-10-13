"""Which account the other leg of a linked transfer sits in.

Both legs of a transfer share one ``transfer_pair_id``; neither row points
at the other. A list that wants to show "From A → To B" needs the other
leg's account, so this looks those up for a whole page in one query
instead of one per row.
"""

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction


async def attach_transfer_counterpart_accounts(
    session: AsyncSession, transactions: Iterable[Transaction]
) -> None:
    """Set ``transfer_counterpart_account_id`` on every paired leg in the batch.

    Unpaired rows, and rows whose other leg is missing, get None. Legs are
    matched within their own workspace, so a pair id never reaches across.
    """
    txs = list(transactions)
    for tx in txs:
        tx.transfer_counterpart_account_id = None
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
    for pair_id, workspace_id, tx_id, account_id in rows.all():
        legs.setdefault((pair_id, workspace_id), []).append((tx_id, account_id))

    for tx in txs:
        if tx.transfer_pair_id is None:
            continue
        others = [
            account_id
            for tx_id, account_id in legs.get((tx.transfer_pair_id, tx.workspace_id), [])
            if tx_id != tx.id
        ]
        tx.transfer_counterpart_account_id = others[0] if others else None
