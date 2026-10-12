"""Loan accounts on real PostgreSQL: the P&L guard in the report SQL and the
secured-asset foreign key. Every amount here is made up."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.app_clock import app_today
from app.core.database import get_async_session
from app.main import app
from app.models.account import Account
from app.models.asset import Asset
from app.models.transaction import Transaction

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


async def test_unpaired_loan_credit_is_not_income_in_the_report(client, auth_headers, session):
    created = await client.post(
        "/api/accounts",
        json={"name": "Test loan", "type": "loan", "balance": "300000", "currency": "BRL",
              "balance_date": "2020-01-01"},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    loan = await session.get(Account, uuid.UUID(created.json()["id"]))
    assert loan is not None
    for amount, tx_type in (("2000.00", "credit"), ("1200.00", "debit")):
        session.add(Transaction(
            id=uuid.uuid4(), user_id=loan.user_id, workspace_id=loan.workspace_id,
            account_id=loan.id, description=f"{tx_type} {amount}", amount=Decimal(amount),
            currency="BRL", date=app_today(), type=tx_type, source="sync", status="posted",
            created_at=datetime.now(timezone.utc),
        ))
    await session.commit()

    response = await client.get(
        "/api/reports/income-expenses", params={"months": 1, "interval": "monthly"}, headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    totals = {b["key"]: b["value"] for b in response.json()["summary"]["breakdowns"]}
    assert totals["income"] == pytest.approx(0.0)
    assert totals["expenses"] == pytest.approx(1200.0)


async def test_deleting_an_asset_row_unlinks_its_loans(session, test_user, test_workspace):
    asset = Asset(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Test house", type="real_estate", currency="BRL",
    )
    session.add(asset)
    await session.flush()
    loan = Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Test loan", type="loan", balance=Decimal("0"), currency="BRL",
        secured_asset_id=asset.id,
    )
    session.add(loan)
    await session.commit()
    loan_id = loan.id

    # The database foreign key alone (ON DELETE SET NULL), no ORM help.
    await session.execute(delete(Asset).where(Asset.id == asset.id))
    await session.commit()

    linked = (
        await session.execute(select(Account.secured_asset_id).where(Account.id == loan_id))
    ).scalar_one()
    assert linked is None
