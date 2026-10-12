"""Loan repayments on real PostgreSQL: the f005 data migration and the report
SQL keeping an unpaired row in the category out of P&L. Every amount and name
here is made up."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.app_clock import app_today
from app.core.database import get_async_session
from app.main import app
from app.models.account import Account
from app.models.transaction import Transaction
from app.services.category_service import create_default_categories, find_default_category
from app.services.loan_repayment_category import LOAN_REPAYMENTS_KEY
from tests.test_loan_repayments_migration import (  # noqa: F401  (collected here on PostgreSQL)
    test_downgrade_removes_only_unused_categories_it_made,
    test_upgrade_adds_it_in_the_workspace_language_beside_transfers,
    test_upgrade_is_idempotent_and_leaves_transactions_alone,
    test_upgrade_skips_unseeded_workspaces_and_ones_that_have_it,
)


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


@pytest.mark.asyncio
async def test_unpaired_rows_in_loan_repayments_stay_out_of_the_report(client, auth_headers, session, test_user, test_workspace):
    await create_default_categories(session, test_user.id, "en", workspace_id=test_workspace.id)
    repayments = await find_default_category(session, test_workspace.id, LOAN_REPAYMENTS_KEY)
    assert repayments is not None
    for name, acc_type in (("Everyday", "checking"), ("Test loan", "loan")):
        account = Account(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, name=name,
            type=acc_type, balance=Decimal("0"), currency="BRL",
        )
        session.add(account)
        for tx_type in ("debit", "credit"):
            session.add(Transaction(
                id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
                account_id=account.id, description=f"Example {tx_type}", amount=Decimal("777"),
                currency="BRL", date=app_today(), type=tx_type, source="sync", status="posted",
                category_id=repayments, created_at=datetime.now(timezone.utc),
            ))
    await session.commit()

    response = await client.get(
        "/api/reports/income-expenses", params={"months": 1, "interval": "monthly"}, headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    totals = {b["key"]: b["value"] for b in response.json()["summary"]["breakdowns"]}
    assert totals["income"] == pytest.approx(0.0)
    assert totals["expenses"] == pytest.approx(0.0)
