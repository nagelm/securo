"""The user's order for accounts.

Every account list in a workspace reads `Account.position`, smallest first.
Ties (two accounts on one position, which only a concurrent create can
produce) fall back to the name the user sees, then the bank's name, then the
id, so a list never shuffles between two reads.

The order is display only: nothing that sums a balance, net worth or P&L
reads `position`.

- New accounts, manual or from bank sync, go after every account in the
  workspace, so they land at the end of whatever group the page shows them
  in.
- Reordering names some of the workspace's accounts in their new order. They
  swap among the places they already held; every other account keeps its
  place. So moving the accounts of one bank connection never moves another
  connection's accounts, and the client can send just the group it shows.
- Sync never writes `position`.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.bank_connection import BankConnection

# ORDER BY for any list of accounts shown to the user.
ACCOUNT_LIST_ORDER = (
    Account.position,
    func.lower(func.coalesce(Account.display_name, Account.name)),
    Account.name,
    Account.id,
)


class AccountOrderError(ValueError):
    """The requested order is malformed (duplicates)."""


class AccountNotFound(LookupError):
    """An account in the requested order is not in the workspace."""


def _in_workspace(workspace_id: uuid.UUID) -> ColumnElement[bool]:
    # Same scope as account_service.get_accounts: connected accounts are
    # reachable through their connection's workspace too.
    return or_(
        Account.workspace_id == workspace_id,
        BankConnection.workspace_id == workspace_id,
    )


async def next_account_position(session: AsyncSession, workspace_id: uuid.UUID) -> int:
    """The position after every account in the workspace (0 when it has none)."""
    result = await session.execute(
        select(func.coalesce(func.max(Account.position), -1) + 1)
        .select_from(Account)
        .outerjoin(BankConnection)
        .where(_in_workspace(workspace_id))
    )
    return int(result.scalar_one())


async def reorder_accounts(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    account_ids: Sequence[uuid.UUID],
) -> None:
    """Put `account_ids` in this order, in the places they already hold.

    The workspace is first renumbered 0..n-1 in its current order, which
    also separates any tied positions, then the named accounts are dealt
    back into their own places in the requested order. Raises
    AccountOrderError for duplicates and AccountNotFound for an id outside
    the workspace; nothing is written in either case. Does not commit.
    """
    if len(set(account_ids)) != len(account_ids):
        raise AccountOrderError("An account is named more than once")

    result = await session.execute(
        select(Account)
        .outerjoin(BankConnection)
        .where(_in_workspace(workspace_id))
        .order_by(*ACCOUNT_LIST_ORDER)
    )
    current = list(result.scalars().all())
    by_id = {account.id: account for account in current}
    if any(account_id not in by_id for account_id in account_ids):
        raise AccountNotFound("Account not found")

    requested = set(account_ids)
    slots = [index for index, account in enumerate(current) if account.id in requested]
    reordered = list(current)
    for slot, account_id in zip(slots, account_ids):
        reordered[slot] = by_id[account_id]

    for position, account in enumerate(reordered):
        if account.position != position:
            account.position = position
