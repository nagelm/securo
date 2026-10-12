"""Recurring loan payments on real PostgreSQL: generation, projections and
the foreign keys SQLite in the other test files does not enforce. Every
amount here is made up."""
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.category import Category
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.schemas.account import AccountCreate
from app.schemas.recurring_transaction import RecurringTransactionCreate
from app.services import loan_payment_service
from app.services.account_service import create_account
from app.services.dashboard_service import get_summary
from app.services.recurring_transaction_service import create_recurring_transaction, generate_pending

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """The reused user fixtures start in a fresh per-test PostgreSQL schema."""


async def _fresh(session, model, row_id):
    return (
        await session.execute(
            select(model).where(model.id == row_id).execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def _setup(session, test_user, test_workspace):
    opened = app_today().replace(day=1) - timedelta(days=40)
    checking = await create_account(
        session, test_workspace.id, test_user.id,
        AccountCreate(name="Everyday", type="checking", balance=Decimal("10000"), currency="BRL",
                      balance_date=opened),
    )
    loan = await create_account(
        session, test_workspace.id, test_user.id,
        AccountCreate(name="Test mortgage", type="loan", balance=Decimal("300000"), currency="BRL",
                      balance_date=opened),
    )
    interest = Category(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Loan interest", icon="tag", color="#000000",
    )
    session.add(interest)
    await session.commit()
    schedule = await create_recurring_transaction(
        session, test_workspace.id, test_user.id,
        RecurringTransactionCreate(
            description="Mortgage payment", amount=Decimal("2000"), currency="BRL", type="debit",
            frequency="yearly", start_date=app_today(), account_id=checking.id,
            kind="loan_payment", transfer_account_id=loan.id,
            interest_amount=Decimal("1200"), interest_category_id=interest.id,
        ),
    )
    return checking, loan, interest, schedule


async def test_projection_then_generation_count_the_interest_once(session, test_user, test_workspace):
    _, loan, _, schedule = await _setup(session, test_user, test_workspace)
    loan_id, schedule_id = loan.id, schedule.id

    projected = await get_summary(session, test_workspace.id, test_user.id)
    assert projected.projected_expenses == pytest.approx(1200.0)

    assert await generate_pending(session, test_user.id, up_to=app_today()) == 3
    payments = await loan_payment_service.list_payments(session, test_workspace.id, loan_account_id=loan_id)
    assert len(payments) == 1 and payments[0].recurring_transaction_id == schedule_id

    generated = await get_summary(session, test_workspace.id, test_user.id)
    assert generated.monthly_expenses == pytest.approx(1200.0)
    assert generated.projected_expenses == pytest.approx(1200.0)


async def test_deleting_the_loan_row_cascades_to_its_schedules(session, test_user, test_workspace):
    """The FK itself, not only account_service: a loan removed by any path
    takes its payment schedules with it."""
    _, loan, _, schedule = await _setup(session, test_user, test_workspace)
    loan_id, schedule_id = loan.id, schedule.id

    # The loan's own rows (its opening balance) go first; the schedule
    # references the loan only through transfer_account_id.
    await session.execute(delete(Transaction).where(Transaction.account_id == loan_id))
    await session.execute(delete(Account).where(Account.id == loan_id))
    await session.commit()

    assert await _fresh(session, RecurringTransaction, schedule_id) is None


async def test_deleting_the_interest_category_row_clears_the_link(session, test_user, test_workspace):
    _, _, interest, schedule = await _setup(session, test_user, test_workspace)
    interest_id, schedule_id = interest.id, schedule.id

    await session.execute(delete(Category).where(Category.id == interest_id))
    await session.commit()

    kept = await _fresh(session, RecurringTransaction, schedule_id)
    assert kept is not None and kept.interest_category_id is None
