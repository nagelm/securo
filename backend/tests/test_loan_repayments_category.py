"""The Loan repayments default category and where repayment legs get it.

Every amount and name here is made up.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.category import Category
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.loan_payment import LoanPaymentCreate
from app.schemas.transaction import TransferCreate
from app.services import transaction_service
from app.services.category_group_service import CATEGORY_TO_GROUP
from app.services.category_service import (
    DEFAULT_CATEGORIES_I18N,
    create_default_categories,
    default_category_names,
    find_default_category,
)
from app.services.loan_payment_service import record_payment
from app.services.loan_repayment_category import LOAN_REPAYMENTS_KEY
from app.services.recurring_transaction_service import generate_pending
from app.services.transfer_detection_service import detect_transfer_pairs
from tests.test_loan_accounts import _manual_loan
from tests.test_loan_pnl_invariant import Book, _category, _checking, _fresh
from tests.test_recurring_loan_payments import _schedule
from tests.test_transfer_matching_rules import _pending

# ---------------------------------------------------------------------------
# the template
# ---------------------------------------------------------------------------


def test_template_is_a_transfer_category_in_the_transfers_group():
    template = DEFAULT_CATEGORIES_I18N[LOAN_REPAYMENTS_KEY]
    assert template["en"] == "Loan repayments"
    assert template["treat_as_transfer"] is True
    assert template["color"] == DEFAULT_CATEGORIES_I18N["transfers"]["color"]
    assert CATEGORY_TO_GROUP[LOAN_REPAYMENTS_KEY] == CATEGORY_TO_GROUP["transfers"]


def test_template_has_a_name_in_every_language_transfers_has():
    transfers_langs = {k for k in DEFAULT_CATEGORIES_I18N["transfers"] if k not in ("icon", "color", "treat_as_transfer")}
    names = default_category_names(LOAN_REPAYMENTS_KEY)
    assert len(names) == len(transfers_langs)
    assert all(DEFAULT_CATEGORIES_I18N[LOAN_REPAYMENTS_KEY].get(lang) for lang in transfers_langs)
    # A name of its own in every language: never mistaken for another default.
    others = {n for key in DEFAULT_CATEGORIES_I18N if key != LOAN_REPAYMENTS_KEY for n in default_category_names(key)}
    assert not set(names) & others


@pytest.mark.asyncio
@pytest.mark.parametrize("lang,name", [("en", "Loan repayments"), ("de", "Kredittilgungen")])
async def test_new_workspaces_get_it(session: AsyncSession, test_user: User, test_workspace: Workspace, lang, name):
    await create_default_categories(session, test_user.id, lang, workspace_id=test_workspace.id)

    cat_id = await find_default_category(session, test_workspace.id, LOAN_REPAYMENTS_KEY)
    assert cat_id is not None
    cat = await session.get(Category, cat_id)
    assert cat is not None
    assert (cat.name, cat.treat_as_transfer, cat.is_system) == (name, True, True)


# ---------------------------------------------------------------------------
# where repayment legs get it
# ---------------------------------------------------------------------------


class Ledger:
    def __init__(self, user, workspace, checking, savings, loan, repayments, transfers, groceries):
        self.user = user
        self.workspace = workspace
        self.checking = checking
        self.savings = savings
        self.loan = loan
        self.repayments = repayments
        self.transfers = transfers
        self.groceries = groceries


async def _ledger(session: AsyncSession, user: User, workspace: Workspace, *, with_repayments: bool = True) -> Ledger:
    await create_default_categories(session, user.id, "en", workspace_id=workspace.id)
    ids = {
        key: await find_default_category(session, workspace.id, key)
        for key in (LOAN_REPAYMENTS_KEY, "transfers", "groceries")
    }
    if not with_repayments:
        # A workspace seeded before the category existed.
        cat = await session.get(Category, ids[LOAN_REPAYMENTS_KEY])
        await session.delete(cat)
        await session.commit()
        ids[LOAN_REPAYMENTS_KEY] = None
    checking = await _checking(session, workspace, user)
    savings = await _checking(session, workspace, user, name="Savings", acc_type="savings")
    loan = await _manual_loan(session, workspace, user, "300000")
    return Ledger(user, workspace, checking, savings, loan, ids[LOAN_REPAYMENTS_KEY], ids["transfers"], ids["groceries"])


@pytest_asyncio.fixture
async def ledger(session: AsyncSession, test_user: User, test_workspace: Workspace) -> Ledger:
    return await _ledger(session, test_user, test_workspace)


async def _row(session, ledger: Ledger, account: Account, tx_type: str, amount="500", category_id=None) -> Transaction:
    tx = Transaction(
        id=uuid.uuid4(), user_id=ledger.user.id, workspace_id=ledger.workspace.id, account_id=account.id,
        description=f"Example {tx_type}", amount=Decimal(amount), currency="BRL", date=app_today(),
        type=tx_type, source="sync", status="posted", category_id=category_id,
        created_at=datetime.now(timezone.utc),
    )
    session.add(tx)
    await session.commit()
    return tx


async def _categories(session, *txs) -> list:
    return [(await _fresh(session, Transaction, tx.id)).category_id for tx in txs]


@pytest.mark.asyncio
async def test_create_transfer_into_a_loan_uses_loan_repayments(session, ledger):
    debit, credit = await transaction_service.create_transfer(
        session, ledger.workspace.id, ledger.user.id,
        TransferCreate(from_account_id=ledger.checking.id, to_account_id=ledger.loan.id,
                       amount=Decimal("800"), date=app_today(), description="Extra repayment"),
    )
    # The response carries the category it was given.
    assert debit.category is not None and debit.category.id == ledger.repayments
    assert await _categories(session, debit, credit) == [ledger.repayments, ledger.repayments]


@pytest.mark.asyncio
async def test_other_transfers_are_left_alone(session, ledger):
    """Only money into a loan is a repayment: a drawdown out of the loan and a
    transfer between two cash accounts keep no category, as before."""
    for source, dest in ((ledger.loan, ledger.checking), (ledger.checking, ledger.savings)):
        debit, credit = await transaction_service.create_transfer(
            session, ledger.workspace.id, ledger.user.id,
            TransferCreate(from_account_id=source.id, to_account_id=dest.id,
                           amount=Decimal("800"), date=app_today(), description="Move"),
        )
        assert await _categories(session, debit, credit) == [None, None]


@pytest.mark.asyncio
async def test_counterpart_and_link_categorise_uncategorised_and_transfers_legs(session, ledger):
    bank_debit = await _row(session, ledger, ledger.checking, "debit", category_id=ledger.transfers)
    debit, credit = await transaction_service.create_transfer_counterpart(
        session, ledger.workspace.id, ledger.user.id, bank_debit.id, ledger.loan.id
    )
    assert await _categories(session, debit, credit) == [ledger.repayments, ledger.repayments]

    a = await _row(session, ledger, ledger.checking, "debit")
    b = await _row(session, ledger, ledger.loan, "credit", category_id=ledger.transfers)
    await transaction_service.link_existing_as_transfer(session, ledger.workspace.id, [a.id, b.id])
    assert await _categories(session, a, b) == [ledger.repayments, ledger.repayments]


@pytest.mark.asyncio
async def test_a_category_the_user_chose_is_kept(session, ledger):
    chosen = await _row(session, ledger, ledger.checking, "debit", category_id=ledger.groceries)
    other = await _row(session, ledger, ledger.loan, "credit")
    await transaction_service.link_existing_as_transfer(session, ledger.workspace.id, [chosen.id, other.id])
    assert await _categories(session, chosen, other) == [ledger.groceries, ledger.repayments]


@pytest.mark.asyncio
async def test_detected_pair_into_a_loan_gets_loan_repayments(session, ledger):
    debit = await _row(session, ledger, ledger.checking, "debit", amount="2000", category_id=ledger.transfers)
    credit = await _row(session, ledger, ledger.loan, "credit", amount="2000")

    assert await detect_transfer_pairs(session, ledger.workspace.id) == 1
    await session.commit()

    fresh = [await _fresh(session, Transaction, tx.id) for tx in (debit, credit)]
    assert fresh[0].transfer_pair_id is not None and fresh[0].transfer_pair_id == fresh[1].transfer_pair_id
    assert [tx.category_id for tx in fresh] == [ledger.repayments, ledger.repayments]


@pytest.mark.asyncio
async def test_manual_payment_falls_back_to_transfers_without_the_category(
    session, test_user: User, test_workspace: Workspace
):
    ledger = await _ledger(session, test_user, test_workspace, with_repayments=False)
    interest = await _category(session, test_workspace, test_user, "Loan interest")
    payment = await record_payment(
        session, test_workspace.id, test_user.id,
        LoanPaymentCreate(
            loan_account_id=ledger.loan.id, cash_account_id=ledger.checking.id, date=app_today(),
            total_amount=Decimal("2000"), interest_amount=Decimal("1200"), interest_category_id=interest.id,
        ),
    )
    cash = await _fresh(session, Transaction, payment.cash_transaction_id)
    leg = await _fresh(session, Transaction, payment.loan_transaction_id)
    assert (cash.category_id, leg.category_id) == (ledger.transfers, ledger.transfers)

    # A generic transfer into the loan has no fallback: it stays as it was.
    debit, credit = await transaction_service.create_transfer(
        session, test_workspace.id, test_user.id,
        TransferCreate(from_account_id=ledger.checking.id, to_account_id=ledger.loan.id,
                       amount=Decimal("100"), date=app_today(), description="Extra"),
    )
    assert await _categories(session, debit, credit) == [None, None]


@pytest.mark.asyncio
async def test_payment_pairing_a_bank_row_moves_it_from_transfers_but_not_from_a_chosen_category(session, ledger):
    interest = await _category(session, ledger.workspace, ledger.user, "Loan interest")
    in_transfers = await _row(session, ledger, ledger.checking, "debit", amount="2000", category_id=ledger.transfers)
    chosen = await _row(session, ledger, ledger.checking, "debit", amount="2000", category_id=ledger.groceries)
    for bank_row, expected in ((in_transfers, ledger.repayments), (chosen, ledger.groceries)):
        payment = await record_payment(
            session, ledger.workspace.id, ledger.user.id,
            LoanPaymentCreate(
                loan_account_id=ledger.loan.id, existing_transaction_id=bank_row.id,
                total_amount=Decimal("2000"), interest_amount=Decimal("1200"), interest_category_id=interest.id,
            ),
        )
        leg = await _fresh(session, Transaction, payment.loan_transaction_id)
        assert await _categories(session, bank_row) == [expected]
        assert leg.category_id == ledger.repayments


@pytest.mark.asyncio
async def test_recurring_loan_payment_legs_use_loan_repayments(session, ledger):
    interest = await _category(session, ledger.workspace, ledger.user, "Loan interest")
    book = Book(ledger.user, ledger.workspace, ledger.checking, ledger.loan, interest)
    await _schedule(session, book, frequency="monthly")
    assert await generate_pending(session, ledger.user.id, up_to=app_today()) == 3

    legs = (
        await session.execute(
            select(Transaction).where(
                Transaction.workspace_id == ledger.workspace.id, Transaction.transfer_pair_id.is_not(None)
            )
        )
    ).scalars().all()
    assert len(legs) == 2
    assert {tx.category_id for tx in legs} == {ledger.repayments}


@pytest.mark.asyncio
async def test_accepting_a_suggested_pair_into_a_loan_gets_loan_repayments(
    client: AsyncClient, auth_headers, session, ledger
):
    first = await _row(session, ledger, ledger.checking, "debit", amount="2000")
    second = await _row(session, ledger, ledger.checking, "debit", amount="2000", category_id=ledger.transfers)
    credit = await _row(session, ledger, ledger.loan, "credit", amount="2000")
    # Two candidates: a question, not a link.
    assert await detect_transfer_pairs(session, ledger.workspace.id) == 0
    await session.commit()
    queue = await _pending(session, ledger.workspace.id)
    assert queue

    resp = await client.post(f"/api/reconciliation/suggestions/{queue[0].id}/accept", headers=auth_headers)
    assert resp.status_code == 200, resp.text

    rows = [await _fresh(session, Transaction, tx.id) for tx in (first, second, credit)]
    paired = [tx for tx in rows if tx.transfer_pair_id is not None]
    assert len(paired) == 2 and credit.id in {tx.id for tx in paired}
    assert {tx.category_id for tx in paired} == {ledger.repayments}
