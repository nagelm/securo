"""The Loan repayments category on the two legs of a loan repayment.

A repayment is a transfer pair whose credit leg is on a ``loan`` account (money
into the loan). Both legs are principal moving between the user's own
accounts, so the category they get is flagged ``treat_as_transfer``: even a
leg that loses its pair stays out of every income and expense figure.

A leg is only given the category when it has none or is in Transfers, so a
category the user (or one of their rules) picked is never replaced.
"""

import uuid
from collections.abc import Iterable
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.transaction import Transaction
from app.services.category_service import find_default_category

LOAN_REPAYMENTS_KEY = "loan_repayments"


async def categorise_loan_repayment_legs(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    legs: Iterable[Optional[Transaction]],
    *,
    fallback_to_transfers: bool = False,
) -> None:
    """Put a repayment pair's legs in Loan repayments. Writes nothing else.

    Does nothing unless a credit leg is on a loan account. When the workspace
    has no Loan repayments category the legs are left alone, or with
    ``fallback_to_transfers`` put in Transfers instead. The caller commits.
    """
    rows = [leg for leg in legs if leg is not None]
    credit_accounts = {leg.account_id for leg in rows if leg.type == "credit"}
    if not credit_accounts:
        return
    loan_credit = (
        await session.execute(
            select(Account.id).where(Account.id.in_(credit_accounts), Account.type == "loan").limit(1)
        )
    ).first()
    if loan_credit is None:
        return

    transfers_id = await find_default_category(session, workspace_id, "transfers")
    target = await find_default_category(session, workspace_id, LOAN_REPAYMENTS_KEY)
    if target is None and fallback_to_transfers:
        target = transfers_id
    if target is None:
        return

    for leg in rows:
        if leg.category_id is None or (transfers_id is not None and leg.category_id == transfers_id):
            leg.category_id = target
