"""Every leg of a transfer pair ends up in a category.

When two rows pair up, a leg with no category goes in the workspace's
Transfers category (repayments into a loan go in Loan repayments first). A
category already on a leg is kept. Every amount and name here is made up.
"""

import pytest
import pytest_asyncio
from decimal import Decimal
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.category import Category
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.transaction import TransferCreate
from app.services import transaction_service
from app.services.transfer_detection_service import detect_transfer_pairs, unlink_transfer_pair
from tests.test_loan_pnl_guard import sqlite_to_char  # noqa: F401  (fixture)
from tests.test_loan_pnl_invariant import PROBES, Book, _fresh
from tests.test_loan_repayments_category import Ledger, _categories, _ledger, _row
from tests.test_transfer_matching_rules import _pending


@pytest_asyncio.fixture
async def ledger(session: AsyncSession, test_user: User, test_workspace: Workspace) -> Ledger:
    return await _ledger(session, test_user, test_workspace)


async def _drop_transfers(session: AsyncSession, ledger: Ledger) -> None:
    """A workspace whose Transfers category was deleted (or renamed)."""
    cat = await session.get(Category, ledger.transfers)
    await session.delete(cat)
    await session.commit()


# ---------------------------------------------------------------------------
# each place a pair forms
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_transfer_puts_both_legs_in_transfers(session, ledger):
    debit, credit = await transaction_service.create_transfer(
        session, ledger.workspace.id, ledger.user.id,
        TransferCreate(from_account_id=ledger.checking.id, to_account_id=ledger.savings.id,
                       amount=Decimal("250"), date=app_today(), description="Weekly savings"),
    )
    assert await _categories(session, debit, credit) == [ledger.transfers, ledger.transfers]


@pytest.mark.asyncio
async def test_counterpart_fills_the_uncategorised_legs_only(session, ledger):
    bare = await _row(session, ledger, ledger.checking, "debit")
    debit, credit = await transaction_service.create_transfer_counterpart(
        session, ledger.workspace.id, ledger.user.id, bare.id, ledger.savings.id
    )
    assert await _categories(session, debit, credit) == [ledger.transfers, ledger.transfers]

    chosen = await _row(session, ledger, ledger.checking, "debit", category_id=ledger.groceries)
    debit, credit = await transaction_service.create_transfer_counterpart(
        session, ledger.workspace.id, ledger.user.id, chosen.id, ledger.savings.id
    )
    assert await _categories(session, debit, credit) == [ledger.groceries, ledger.transfers]


@pytest.mark.asyncio
async def test_linking_two_rows_keeps_a_chosen_category(session, ledger):
    chosen = await _row(session, ledger, ledger.checking, "debit", category_id=ledger.groceries)
    bare = await _row(session, ledger, ledger.savings, "credit")
    await transaction_service.link_existing_as_transfer(session, ledger.workspace.id, [chosen.id, bare.id])
    assert await _categories(session, chosen, bare) == [ledger.groceries, ledger.transfers]


@pytest.mark.asyncio
async def test_detected_pair_fills_the_leg_the_bank_gave_no_category(session, ledger):
    """The automatic-payment shape: the bank hinted Transfers on the debit,
    and the matching credit into savings came with nothing."""
    debit = await _row(session, ledger, ledger.checking, "debit", amount="75", category_id=ledger.transfers)
    credit = await _row(session, ledger, ledger.savings, "credit", amount="75")

    assert await detect_transfer_pairs(session, ledger.workspace.id) == 1
    await session.commit()

    fresh = [await _fresh(session, Transaction, tx.id) for tx in (debit, credit)]
    assert fresh[0].transfer_pair_id is not None and fresh[0].transfer_pair_id == fresh[1].transfer_pair_id
    assert [tx.category_id for tx in fresh] == [ledger.transfers, ledger.transfers]


@pytest.mark.asyncio
async def test_detected_pair_keeps_a_chosen_category(session, ledger):
    debit = await _row(session, ledger, ledger.checking, "debit", amount="75", category_id=ledger.groceries)
    credit = await _row(session, ledger, ledger.savings, "credit", amount="75")

    assert await detect_transfer_pairs(session, ledger.workspace.id) == 1
    await session.commit()

    assert await _categories(session, debit, credit) == [ledger.groceries, ledger.transfers]


@pytest.mark.asyncio
async def test_accepting_a_suggested_pair_fills_uncategorised_legs(
    client: AsyncClient, auth_headers, session, ledger
):
    first = await _row(session, ledger, ledger.checking, "debit", amount="75")
    second = await _row(session, ledger, ledger.checking, "debit", amount="75")
    credit = await _row(session, ledger, ledger.savings, "credit", amount="75")
    # Two candidates: a question, not a link.
    assert await detect_transfer_pairs(session, ledger.workspace.id) == 0
    await session.commit()
    queue = await _pending(session, ledger.workspace.id)
    assert queue

    resp = await client.post(f"/api/reconciliation/suggestions/{queue[0].id}/accept", headers=auth_headers)
    assert resp.status_code == 200, resp.text

    rows = [await _fresh(session, Transaction, tx.id) for tx in (first, second, credit)]
    paired = [tx for tx in rows if tx.transfer_pair_id is not None]
    unpaired = [tx for tx in rows if tx.transfer_pair_id is None]
    assert len(paired) == 2 and credit.id in {tx.id for tx in paired}
    assert {tx.category_id for tx in paired} == {ledger.transfers}
    # The row left out of the pair is not a transfer and keeps no category.
    assert [tx.category_id for tx in unpaired] == [None]


# ---------------------------------------------------------------------------
# loans, a missing category, unpairing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_loan_repayment_still_gets_loan_repayments(session, ledger):
    debit, credit = await transaction_service.create_transfer(
        session, ledger.workspace.id, ledger.user.id,
        TransferCreate(from_account_id=ledger.checking.id, to_account_id=ledger.loan.id,
                       amount=Decimal("800"), date=app_today(), description="Extra repayment"),
    )
    assert await _categories(session, debit, credit) == [ledger.repayments, ledger.repayments]


@pytest.mark.asyncio
async def test_a_workspace_without_transfers_leaves_the_legs_alone(session, ledger):
    await _drop_transfers(session, ledger)

    debit, credit = await transaction_service.create_transfer(
        session, ledger.workspace.id, ledger.user.id,
        TransferCreate(from_account_id=ledger.checking.id, to_account_id=ledger.savings.id,
                       amount=Decimal("250"), date=app_today(), description="Weekly savings"),
    )
    assert await _categories(session, debit, credit) == [None, None]

    a = await _row(session, ledger, ledger.checking, "debit", amount="75")
    b = await _row(session, ledger, ledger.savings, "credit", amount="75")
    assert await detect_transfer_pairs(session, ledger.workspace.id) == 1
    await session.commit()
    assert await _categories(session, a, b) == [None, None]


@pytest.mark.asyncio
async def test_unpairing_keeps_the_categories(session, ledger):
    chosen = await _row(session, ledger, ledger.checking, "debit", category_id=ledger.groceries)
    bare = await _row(session, ledger, ledger.savings, "credit")
    debit, _ = await transaction_service.link_existing_as_transfer(
        session, ledger.workspace.id, [chosen.id, bare.id]
    )
    pair_id = debit.transfer_pair_id
    assert pair_id is not None

    assert await unlink_transfer_pair(session, ledger.workspace.id, pair_id) == 2
    await session.commit()

    fresh = [await _fresh(session, Transaction, tx.id) for tx in (chosen, bare)]
    assert [tx.transfer_pair_id for tx in fresh] == [None, None]
    assert [tx.category_id for tx in fresh] == [ledger.groceries, ledger.transfers]


# ---------------------------------------------------------------------------
# P&L: filling a leg with Transfers moves no income or expense figure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("probe", list(PROBES), ids=list(PROBES))
async def test_filling_a_leg_with_transfers_moves_no_pnl_figure(session, ledger, sqlite_to_char, probe):  # noqa: F811
    measure = PROBES[probe]
    # The probes' category bucket is pointed at Transfers.
    book = Book(ledger.user, ledger.workspace, ledger.checking, ledger.loan,
                await session.get(Category, ledger.transfers))
    before = await measure(session, book)

    await _row(session, ledger, ledger.checking, "debit", amount="75", category_id=ledger.transfers)
    credit = await _row(session, ledger, ledger.savings, "credit", amount="75")
    assert await detect_transfer_pairs(session, ledger.workspace.id) == 1
    await session.commit()
    assert await _categories(session, credit) == [ledger.transfers]

    after = await measure(session, book)

    # The same pair with the credit leg left uncategorised, as before the fix.
    leg = await _fresh(session, Transaction, credit.id)
    leg.category_id = None
    await session.commit()
    uncategorised = await measure(session, book)

    # Paired legs are out of P&L whatever their category: the figures match
    # both the book before the pair existed and the pair left uncategorised.
    assert after == pytest.approx(before)
    assert uncategorised == pytest.approx(before)
