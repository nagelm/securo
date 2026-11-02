"""The category the legs of a transfer pair get when the pair forms.

Every place that pairs two rows calls `categorise_pair_legs`, so they all
agree. A repayment into a loan follows the Loan repayments rule
(`categorise_loan_repayment_legs`); after that, a leg still without a category
goes in the workspace's Transfers category. A pair is money moving between
the user's own accounts, and a leg the bank gave no hint for would otherwise
show as a transfer with no category.

A category that is already set is never replaced. Transfers is flagged
``treat_as_transfer`` and paired legs are out of P&L anyway, so no income or
expense figure changes. Unpairing leaves categories as they are.
"""

import uuid
from collections.abc import Iterable
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction
from app.services.category_service import find_default_category
from app.services.loan_repayment_category import categorise_loan_repayment_legs

TRANSFERS_KEY = "transfers"


async def categorise_pair_legs(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    legs: Iterable[Optional[Transaction]],
) -> None:
    """Categorise the legs of a pair that just formed. The caller commits.

    The loan rule goes first; then each leg with no category is put in
    Transfers. A workspace without a Transfers category is left alone.
    """
    rows = [leg for leg in legs if leg is not None]
    await categorise_loan_repayment_legs(session, workspace_id, rows)
    if all(leg.category_id is not None for leg in rows):
        return
    transfers_id = await find_default_category(session, workspace_id, TRANSFERS_KEY)
    if transfers_id is None:
        return
    for leg in rows:
        if leg.category_id is None:
            leg.category_id = transfers_id
