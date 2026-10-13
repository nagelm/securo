"""A revolving loan's credit limit (loan_details.type == "REVOLVING").

The limit lives in `Account.credit_limit`, the column a credit card uses, and
reads like a card's: limit, amount owed (-balance) and available to draw
(limit - owed, never below zero). Only a revolving loan keeps one. It is
display only: no balance, net worth, P&L or projection moves with it. Every
amount here is made up.
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.category import Category
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.account import AccountCreate, AccountUpdate
from app.services import account_service
from app.services.account_service import create_account, get_account_summary, update_account
from app.services.dashboard_service import get_summary
from app.services.report_service import (
    get_cash_flow_report,
    get_income_expenses_report,
    get_net_worth_report,
)
from tests.test_connection_service import (
    _akahu_connection,
    _connected_account,
    _loan_data,
    _loan_provider,
    _sync_with,
)
from tests.test_loan_accounts import _connection
from tests.test_loan_pnl_guard import sqlite_to_char  # noqa: F401  (fixture)

REVOLVING = {"type": "REVOLVING", "interest": {"rate": 7.5}}
TABLE = {"type": "TABLE"}


def _opened():
    return app_today().replace(day=1) - timedelta(days=40)


async def _loan(
    session: AsyncSession,
    workspace: Workspace,
    user: User,
    owed: str,
    *,
    details: dict | None = REVOLVING,
    limit: str | None = None,
) -> Account:
    return await create_account(
        session,
        workspace.id,
        user.id,
        AccountCreate(
            name="Test revolving credit",
            type="loan",
            balance=Decimal(owed),
            currency="BRL",
            balance_date=_opened(),
            loan_details=details,
            credit_limit=Decimal(limit) if limit is not None else None,
        ),
    )


async def _row(session: AsyncSession, workspace: Workspace, account_id: uuid.UUID) -> dict:
    accounts = await account_service.get_accounts(session, workspace.id)
    return next(a for a in accounts if a["id"] == account_id)


# ---------------------------------------------------------------------------
# create and read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revolving_loan_keeps_its_limit_and_reads_like_a_card(session, test_user, test_workspace):
    loan = await _loan(session, test_workspace, test_user, "4000", limit="10000")

    assert loan.credit_limit == Decimal("10000.00")
    row = await _row(session, test_workspace, loan.id)
    assert row["current_balance"] == pytest.approx(-4000.0)
    assert row["credit_limit"] == pytest.approx(10000.0)
    assert row["available_credit"] == pytest.approx(6000.0)


@pytest.mark.asyncio
async def test_revolving_loan_without_a_limit_has_no_available_credit(session, test_user, test_workspace):
    loan = await _loan(session, test_workspace, test_user, "4000")

    row = await _row(session, test_workspace, loan.id)
    assert row["credit_limit"] is None
    assert row["available_credit"] is None


@pytest.mark.asyncio
async def test_available_never_goes_below_zero(session, test_user, test_workspace):
    loan = await _loan(session, test_workspace, test_user, "12000", limit="10000")

    row = await _row(session, test_workspace, loan.id)
    assert row["available_credit"] == 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize("entered", ["0", "-250"])
async def test_nothing_owed_leaves_the_whole_limit_available(session, test_user, test_workspace, entered):
    # "-250" owed is a loan in credit: it owes nothing.
    loan = await _loan(session, test_workspace, test_user, entered, limit="10000")

    row = await _row(session, test_workspace, loan.id)
    assert row["current_balance"] >= 0
    assert row["available_credit"] == pytest.approx(10000.0)


@pytest.mark.asyncio
async def test_connected_revolving_loan_reads_the_provider_balance(session, test_user, test_workspace):
    conn = await _connection(session, test_user)
    loan = Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, connection_id=conn.id,
        name="Synced revolving", type="loan", balance=Decimal("-2500"), currency="BRL",
        loan_details=REVOLVING, credit_limit=Decimal("8000"),
    )
    session.add(loan)
    await session.commit()

    row = await _row(session, test_workspace, loan.id)
    assert row["current_balance"] == pytest.approx(-2500.0)
    assert row["available_credit"] == pytest.approx(5500.0)


@pytest.mark.asyncio
async def test_a_stored_limit_is_hidden_while_the_loan_is_not_revolving(session, test_user, test_workspace):
    # Sync may change the loan type; the stored limit then stays but is not shown.
    conn = await _connection(session, test_user)
    loan = Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, connection_id=conn.id,
        name="Synced loan", type="loan", balance=Decimal("-2500"), currency="BRL",
        loan_details=TABLE, credit_limit=Decimal("8000"),
    )
    session.add(loan)
    await session.commit()

    row = await _row(session, test_workspace, loan.id)
    assert row["credit_limit"] is None
    assert row["available_credit"] is None


# ---------------------------------------------------------------------------
# only a revolving loan takes a limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("details", [None, TABLE, {"type": "UNKNOWN"}])
async def test_a_limit_on_a_loan_that_is_not_revolving_is_refused(session, test_user, test_workspace, details):
    with pytest.raises(ValueError, match="revolving"):
        await _loan(session, test_workspace, test_user, "4000", details=details, limit="10000")


@pytest.mark.asyncio
async def test_a_negative_limit_is_refused(session, test_user, test_workspace):
    with pytest.raises(ValueError, match="credit_limit"):
        await _loan(session, test_workspace, test_user, "4000", limit="-1")


@pytest.mark.asyncio
async def test_limit_over_api(client: AsyncClient, auth_headers):
    created = await client.post(
        "/api/accounts",
        json={
            "name": "Test revolving credit", "type": "loan", "balance": "1500", "currency": "BRL",
            "loan_details": {"type": "revolving"}, "credit_limit": "5000",
        },
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["credit_limit"] == pytest.approx(5000.0)

    refused = await client.post(
        "/api/accounts",
        json={"name": "Test term loan", "type": "loan", "loan_details": {"type": "TABLE"}, "credit_limit": "5000"},
        headers=auth_headers,
    )
    assert refused.status_code == 400

    listed = await client.get("/api/accounts", headers=auth_headers)
    row = next(a for a in listed.json() if a["id"] == body["id"])
    assert row["available_credit"] == pytest.approx(3500.0)


@pytest.mark.asyncio
async def test_limit_can_be_set_changed_and_cleared(session, test_user, test_workspace):
    loan = await _loan(session, test_workspace, test_user, "4000")

    await update_account(session, loan.id, test_workspace.id, AccountUpdate(credit_limit=Decimal("9000")))
    assert (await _row(session, test_workspace, loan.id))["available_credit"] == pytest.approx(5000.0)

    await update_account(session, loan.id, test_workspace.id, AccountUpdate(credit_limit=None))
    row = await _row(session, test_workspace, loan.id)
    assert row["credit_limit"] is None
    assert row["available_credit"] is None


@pytest.mark.asyncio
async def test_making_a_loan_revolving_and_setting_its_limit_in_one_request(session, test_user, test_workspace):
    loan = await _loan(session, test_workspace, test_user, "4000", details=TABLE)

    updated = await update_account(
        session, loan.id, test_workspace.id,
        AccountUpdate(loan_details=REVOLVING, credit_limit=Decimal("6000")),
    )
    assert updated is not None
    assert updated.credit_limit == Decimal("6000.00")


@pytest.mark.asyncio
async def test_unrelated_edits_keep_the_limit(session, test_user, test_workspace):
    loan = await _loan(session, test_workspace, test_user, "4000", limit="10000")

    updated = await update_account(session, loan.id, test_workspace.id, AccountUpdate(name="Renamed"))
    assert updated is not None
    assert updated.credit_limit == Decimal("10000.00")


@pytest.mark.asyncio
@pytest.mark.parametrize("details", [TABLE, None])
async def test_limit_cleared_when_the_loan_stops_being_revolving(session, test_user, test_workspace, details):
    loan = await _loan(session, test_workspace, test_user, "4000", limit="10000")

    updated = await update_account(session, loan.id, test_workspace.id, AccountUpdate(loan_details=details))
    assert updated is not None
    assert updated.credit_limit is None


@pytest.mark.asyncio
async def test_limit_cleared_when_the_account_stops_being_a_loan(session, test_user, test_workspace):
    loan = await _loan(session, test_workspace, test_user, "4000", limit="10000")

    updated = await update_account(session, loan.id, test_workspace.id, AccountUpdate(type="checking"))
    assert updated is not None
    assert updated.credit_limit is None


@pytest.mark.asyncio
async def test_limit_on_a_non_revolving_update_writes_nothing(session, test_user, test_workspace):
    loan = await _loan(session, test_workspace, test_user, "4000", details=TABLE)

    with pytest.raises(ValueError, match="revolving"):
        await update_account(
            session, loan.id, test_workspace.id,
            AccountUpdate(name="Renamed", credit_limit=Decimal("5000")),
        )
    await session.refresh(loan)
    assert loan.name == "Test revolving credit"
    assert loan.credit_limit is None


# ---------------------------------------------------------------------------
# bank-connected revolving loans
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connected_revolving_loan_takes_a_user_limit(session, test_user, test_workspace):
    conn = await _connection(session, test_user)
    loan = Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, connection_id=conn.id,
        name="Synced revolving", type="loan", balance=Decimal("-2500"), currency="BRL",
    )
    session.add(loan)
    await session.commit()

    with pytest.raises(ValueError, match="revolving"):
        await update_account(session, loan.id, test_workspace.id, AccountUpdate(credit_limit=Decimal("8000")))

    updated = await update_account(
        session, loan.id, test_workspace.id,
        AccountUpdate(loan_details=REVOLVING, credit_limit=Decimal("8000")),
    )
    assert updated is not None
    assert updated.credit_limit == Decimal("8000.00")

    # Other card fields stay refused on a loan.
    with pytest.raises(ValueError, match="Credit card fields"):
        await update_account(session, loan.id, test_workspace.id, AccountUpdate(statement_close_day=5))

    updated = await update_account(session, loan.id, test_workspace.id, AccountUpdate(loan_details=TABLE))
    assert updated is not None
    assert updated.credit_limit is None


@pytest.mark.asyncio
async def test_sync_never_clears_a_revolving_loans_limit(session, test_user, test_workspace):
    conn = await _akahu_connection(session, test_user.id)
    loan = _connected_account(
        conn, test_user.id, test_workspace.id, "loan-1", "loan",
        loan_details=REVOLVING, credit_limit=Decimal("20000"),
    )
    session.add(loan)
    await session.commit()

    # The provider sends no limit for a loan (AccountData.credit_limit None).
    await _sync_with(
        session, conn, test_workspace.id, test_user.id,
        _loan_provider(_loan_data(loan_details={"type": "REVOLVING", "interest": {"rate": 8.0}})),
    )
    await session.refresh(loan)
    assert loan.credit_limit == Decimal("20000.00")
    assert loan.loan_details == {"type": "REVOLVING", "interest": {"rate": 8.0}}

    # Nor does a feed without loan details.
    await _sync_with(
        session, conn, test_workspace.id, test_user.id,
        _loan_provider(_loan_data(loan_details=None)),
    )
    await session.refresh(loan)
    assert loan.credit_limit == Decimal("20000.00")
    row = await _row(session, test_workspace, loan.id)
    # The synced balance owes 300,000, over the limit: nothing is available.
    assert row["available_credit"] == 0.0


# ---------------------------------------------------------------------------
# display only: net worth, P&L and projections ignore the limit
# ---------------------------------------------------------------------------


async def _snapshot(session: AsyncSession, workspace: Workspace, user: User, loan: Account) -> dict:
    summary = await get_summary(session, workspace.id, user.id)
    net_worth = await get_net_worth_report(session, workspace.id, user.id, months=3, currency="BRL")
    income_expenses = await get_income_expenses_report(
        session, workspace.id, user.id, months=3, interval="monthly", currency="BRL"
    )
    cash_flow = await get_cash_flow_report(
        session, workspace.id, user.id, months=1, interval="daily", currency="BRL"
    )
    loan_stats = await get_account_summary(session, loan.id, workspace.id)
    row = await _row(session, workspace, loan.id)
    return {
        "summary": summary.model_dump(mode="json"),
        "net_worth": net_worth.model_dump(mode="json"),
        "income_expenses": income_expenses.model_dump(mode="json"),
        "cash_flow": cash_flow.model_dump(mode="json"),
        "loan_stats": loan_stats,
        "balance": row["current_balance"],
    }


@pytest.mark.asyncio
async def test_net_worth_pnl_and_projections_ignore_the_limit(
    session, test_user, test_workspace, sqlite_to_char  # noqa: F811
):
    await create_account(
        session, test_workspace.id, test_user.id,
        AccountCreate(name="Everyday", type="checking", balance=Decimal("10000"), currency="BRL",
                      balance_date=_opened()),
    )
    loan = await _loan(session, test_workspace, test_user, "4000")
    spending = Category(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, name="Test spending",
        icon="tag", color="#000000",
    )
    session.add(spending)
    # A draw on the line spent straight from it, and an interest charge.
    for description, amount in (("Draw", "750"), ("Interest", "25")):
        session.add(Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, account_id=loan.id,
            description=description, amount=Decimal(amount), currency="BRL", date=app_today(),
            type="debit", source="manual", status="posted", category_id=spending.id,
            created_at=datetime.now(timezone.utc),
        ))
    await session.commit()

    without_limit = await _snapshot(session, test_workspace, test_user, loan)
    await update_account(session, loan.id, test_workspace.id, AccountUpdate(credit_limit=Decimal("10000")))
    with_limit = await _snapshot(session, test_workspace, test_user, loan)

    assert without_limit["balance"] == pytest.approx(-4775.0)
    assert without_limit["summary"]["monthly_expenses"] == pytest.approx(775.0)
    assert with_limit == without_limit
    assert (await _row(session, test_workspace, loan.id))["available_credit"] == pytest.approx(5225.0)
