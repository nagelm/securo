"""Loan payments on real PostgreSQL: the foreign keys that keep the record
in step with the ledger (SQLite in the other test files does not enforce
them). Every amount here is made up."""
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.app_clock import app_today
from app.core.database import get_async_session
from app.main import app
from app.models.category import Category
from app.models.loan_payment import LoanPayment
from app.models.transaction import Transaction
from app.schemas.account import AccountCreate
from app.schemas.loan_payment import LoanPaymentCreate
from app.services.account_service import create_account
from app.services.dashboard_service import get_summary
from app.services.loan_payment_service import record_payment

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """The reused user fixtures start in a fresh per-test PostgreSQL schema."""


@pytest_asyncio.fixture
async def client(postgres_sessions, monkeypatch):
    async def pg_session():
        async with postgres_sessions() as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_async_session, pg_session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


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
    payment = await record_payment(
        session, test_workspace.id, test_user.id,
        LoanPaymentCreate(
            loan_account_id=loan.id, cash_account_id=checking.id, date=app_today(),
            total_amount=Decimal("2000"), interest_amount=Decimal("1200"),
            interest_category_id=interest.id,
        ),
    )
    return checking, loan, interest, payment


async def test_delete_leg_removes_record_and_keeps_interest(client, auth_headers, session, test_user, test_workspace):
    """Deleting a leg through the ordinary transaction UI deletes both legs;
    the record cascades away and the interest row stays an expense."""
    _, _, interest, payment = await _setup(session, test_user, test_workspace)

    response = await client.delete(f"/api/transactions/{payment.loan_transaction_id}", headers=auth_headers)
    assert response.status_code in (200, 204), response.text

    assert await _fresh(session, LoanPayment, payment.id) is None
    assert await _fresh(session, Transaction, payment.cash_transaction_id) is None
    kept = await _fresh(session, Transaction, payment.interest_transaction_id)
    assert kept is not None and kept.category_id == interest.id
    summary = await get_summary(session, test_workspace.id, test_user.id)
    assert summary.monthly_expenses == pytest.approx(1200.0)


async def test_bulk_delete_of_a_leg_cascades_the_record(client, auth_headers, session, test_user, test_workspace):
    _, _, _, payment = await _setup(session, test_user, test_workspace)

    response = await client.post(
        "/api/transactions/bulk-delete",
        json={"transaction_ids": [str(payment.cash_transaction_id)]},
        headers=auth_headers,
    )
    assert response.status_code in (200, 204), response.text
    assert await _fresh(session, LoanPayment, payment.id) is None


async def test_deleting_the_interest_row_only_unlinks_it(client, auth_headers, session, test_user, test_workspace):
    _, _, _, payment = await _setup(session, test_user, test_workspace)

    response = await client.delete(f"/api/transactions/{payment.interest_transaction_id}", headers=auth_headers)
    assert response.status_code in (200, 204), response.text

    record = await _fresh(session, LoanPayment, payment.id)
    assert record is not None and record.interest_transaction_id is None
    listed = await client.get("/api/loan-payments", headers=auth_headers)
    assert listed.status_code == 200
    (row,) = listed.json()
    assert Decimal(row["interest"]) == Decimal("0") and Decimal(row["principal"]) == Decimal("2000")


async def test_payment_and_unlink_guard_on_postgres(client, auth_headers, session, test_user, test_workspace):
    checking, loan, _, payment = await _setup(session, test_user, test_workspace)
    cash = await _fresh(session, Transaction, payment.cash_transaction_id)
    assert cash is not None

    unlink = await client.delete(f"/api/connections/transfers/{cash.transfer_pair_id}", headers=auth_headers)
    assert unlink.status_code == 409, unlink.text

    report = await client.get(
        "/api/reports/income-expenses", params={"months": 1, "interval": "monthly"}, headers=auth_headers,
    )
    assert report.status_code == 200, report.text
    totals = {b["key"]: b["value"] for b in report.json()["summary"]["breakdowns"]}
    assert totals["income"] == pytest.approx(0.0)
    assert totals["expenses"] == pytest.approx(1200.0)

    summary = await client.get(f"/api/accounts/{loan.id}/loan-summary", headers=auth_headers)
    assert summary.status_code == 200, summary.text
    assert summary.json()["months"][-1]["principal_reduction"] == pytest.approx(800.0)
