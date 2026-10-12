"""Loan accounts (type="loan"): creation sign, loan fields, net worth.

A loan's balance is negative (the amount owed). The P&L guard for loan
credits is in `test_loan_pnl_guard.py`. Every amount here is made up.
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.asset import Asset
from app.models.bank_connection import BankConnection
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.account import AccountCreate, AccountUpdate
from app.services import account_service, asset_service
from app.services.account_service import (
    LOAN_SUBTYPES,
    create_account,
    serialize_account,
    update_account,
)
from app.services.reconciliation_rule_service import _validate_account_types
from app.services.report_service import _net_worth_at


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _account(
    session: AsyncSession,
    user: User,
    *,
    name: str,
    acc_type: str,
    balance: str = "0.00",
    connection_id: uuid.UUID | None = None,
) -> Account:
    account = Account(
        id=uuid.uuid4(),
        user_id=user.id,
        name=name,
        type=acc_type,
        balance=Decimal(balance),
        currency="BRL",
        connection_id=connection_id,
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


async def _tx(
    session: AsyncSession,
    user: User,
    account: Account,
    amount: str,
    tx_type: str,
    *,
    when=None,
    source: str = "manual",
    status: str = "posted",
    category_id: uuid.UUID | None = None,
    transfer_pair_id: uuid.UUID | None = None,
) -> Transaction:
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=user.id,
        account_id=account.id,
        description=f"{tx_type} {amount}",
        amount=Decimal(amount),
        currency="BRL",
        date=when or app_today(),
        type=tx_type,
        source=source,
        status=status,
        category_id=category_id,
        transfer_pair_id=transfer_pair_id,
        created_at=datetime.now(timezone.utc),
    )
    session.add(tx)
    await session.commit()
    await session.refresh(tx)
    return tx


async def _asset(
    session: AsyncSession, user: User, workspace_id: uuid.UUID, *, asset_type: str = "real_estate"
) -> Asset:
    asset = Asset(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace_id,
        name=f"Test {asset_type}",
        type=asset_type,
        currency="BRL",
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
    return asset


async def _connection(session: AsyncSession, user: User) -> BankConnection:
    conn = BankConnection(
        id=uuid.uuid4(),
        user_id=user.id,
        provider="test",
        external_id=f"ext-{uuid.uuid4().hex[:8]}",
        institution_name="Test Bank",
        credentials={},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.commit()
    await session.refresh(conn)
    return conn


async def _manual_loan(session: AsyncSession, workspace: Workspace, user: User, owed: str) -> Account:
    return await create_account(
        session,
        workspace.id,
        user.id,
        AccountCreate(
            name="Test mortgage", type="loan", balance=Decimal(owed), currency="BRL",
            # Opened before this month, like a real loan: the per-account
            # summary counts an opening row dated inside its window.
            balance_date=app_today().replace(day=1) - timedelta(days=40),
        ),
    )


async def _balance(session: AsyncSession, workspace: Workspace, account_id: uuid.UUID) -> float:
    accounts = await account_service.get_accounts(session, workspace.id)
    return next(a["current_balance"] for a in accounts if a["id"] == account_id)


# ---------------------------------------------------------------------------
# creation and sign
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_loan_amount_owed_reads_negative(session, test_user, test_workspace):
    loan = await _manual_loan(session, test_workspace, test_user, "300000")

    assert loan.type == "loan"
    assert await _balance(session, test_workspace, loan.id) == pytest.approx(-300000.0)

    opening = (
        await session.execute(
            select(Transaction).where(
                Transaction.account_id == loan.id, Transaction.source == "opening_balance"
            )
        )
    ).scalar_one()
    assert opening.type == "debit"
    assert opening.amount == Decimal("300000.00")


@pytest.mark.asyncio
async def test_manual_loan_balance_edit_keeps_debt_sign(session, test_user, test_workspace):
    loan = await _manual_loan(session, test_workspace, test_user, "300000")

    await update_account(session, loan.id, test_workspace.id, AccountUpdate(balance=Decimal("250000")))

    assert await _balance(session, test_workspace, loan.id) == pytest.approx(-250000.0)


@pytest.mark.asyncio
async def test_create_loan_over_api(client: AsyncClient, auth_headers, session, test_user, test_workspace):
    asset = await _asset(session, test_user, test_workspace.id)

    response = await client.post(
        "/api/accounts",
        json={
            "name": "Test home loan",
            "type": "loan",
            "balance": "300000",
            "currency": "BRL",
            "loan_subtype": "mortgage",
            "secured_asset_id": str(asset.id),
        },
        headers=auth_headers,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["type"] == "loan"
    assert body["loan_subtype"] == "mortgage"
    assert body["secured_asset_id"] == str(asset.id)
    assert body["loan_details"] is None

    listed = await client.get("/api/accounts", headers=auth_headers)
    row = next(a for a in listed.json() if a["id"] == body["id"])
    assert row["current_balance"] == pytest.approx(-300000.0)


@pytest.mark.asyncio
async def test_connected_loan_keeps_provider_sign(session, test_user):
    conn = await _connection(session, test_user)
    loan = await _account(
        session, test_user, name="Synced loan", acc_type="loan", balance="-300000", connection_id=conn.id
    )

    payload = serialize_account(loan, None, None, conn)

    assert payload["current_balance"] == pytest.approx(-300000.0)


# ---------------------------------------------------------------------------
# loan fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("subtype", LOAN_SUBTYPES)
async def test_every_loan_subtype_is_accepted(session, test_user, test_workspace, subtype):
    loan = await create_account(
        session,
        test_workspace.id,
        test_user.id,
        AccountCreate(name="Loan", type="loan", balance=Decimal("1000"), currency="BRL", loan_subtype=subtype),
    )
    assert loan.loan_subtype == subtype


@pytest.mark.asyncio
async def test_invalid_loan_subtype_is_a_400(client: AsyncClient, auth_headers):
    response = await client.post(
        "/api/accounts",
        json={"name": "Loan", "type": "loan", "balance": "1000", "loan_subtype": "boat"},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "loan subtype" in response.json()["detail"]


@pytest.mark.asyncio
async def test_loan_fields_on_a_non_loan_account_are_a_400(
    client: AsyncClient, auth_headers, session, test_user, test_workspace
):
    asset = await _asset(session, test_user, test_workspace.id)

    by_subtype = await client.post(
        "/api/accounts",
        json={"name": "Everyday", "type": "checking", "loan_subtype": "mortgage"},
        headers=auth_headers,
    )
    by_asset = await client.post(
        "/api/accounts",
        json={"name": "Everyday", "type": "checking", "secured_asset_id": str(asset.id)},
        headers=auth_headers,
    )

    assert by_subtype.status_code == 400
    assert by_asset.status_code == 400


@pytest.mark.asyncio
async def test_invalid_subtype_on_update_writes_nothing(session, test_user, test_workspace):
    loan = await _manual_loan(session, test_workspace, test_user, "1000")
    loan_id, workspace_id = loan.id, test_workspace.id

    with pytest.raises(ValueError):
        await update_account(
            session, loan_id, workspace_id, AccountUpdate(name="Renamed", loan_subtype="boat")
        )
    await session.rollback()

    reread = await account_service.get_account(session, loan_id, workspace_id)
    assert reread is not None
    assert reread.name == "Test mortgage"
    assert reread.loan_subtype is None


@pytest.mark.asyncio
@pytest.mark.parametrize("asset_type", ["real_estate", "vehicle", "valuable", "investment", "other"])
async def test_loan_can_be_secured_against_any_asset_type(session, test_user, test_workspace, asset_type):
    asset = await _asset(session, test_user, test_workspace.id, asset_type=asset_type)
    loan = await _manual_loan(session, test_workspace, test_user, "1000")

    updated = await update_account(
        session, loan.id, test_workspace.id, AccountUpdate(secured_asset_id=asset.id)
    )

    assert updated is not None
    assert updated.secured_asset_id == asset.id


@pytest.mark.asyncio
async def test_secured_asset_must_be_in_the_same_workspace(session, test_user, test_workspace):
    other = Workspace(
        id=uuid.uuid4(),
        name="Other",
        kind="shared",
        created_by_user_id=test_user.id,
        default_currency="BRL",
    )
    session.add(other)
    await session.commit()
    foreign_asset = await _asset(session, test_user, other.id)
    loan = await _manual_loan(session, test_workspace, test_user, "1000")

    with pytest.raises(ValueError, match="Linked asset not found"):
        await update_account(
            session, loan.id, test_workspace.id, AccountUpdate(secured_asset_id=foreign_asset.id)
        )
    with pytest.raises(ValueError, match="Linked asset not found"):
        await create_account(
            session,
            test_workspace.id,
            test_user.id,
            AccountCreate(name="Loan", type="loan", balance=Decimal("1"), secured_asset_id=foreign_asset.id),
        )
    with pytest.raises(ValueError, match="Linked asset not found"):
        await update_account(
            session, loan.id, test_workspace.id, AccountUpdate(secured_asset_id=uuid.uuid4())
        )


@pytest.mark.asyncio
async def test_loan_fields_cleared_when_type_leaves_loan(session, test_user, test_workspace):
    asset = await _asset(session, test_user, test_workspace.id)
    loan = await create_account(
        session,
        test_workspace.id,
        test_user.id,
        AccountCreate(
            name="Loan", type="loan", balance=Decimal("1000"), currency="BRL",
            loan_subtype="personal", secured_asset_id=asset.id,
        ),
    )
    loan.loan_details = {"interest_rate": 5.5}
    await session.commit()

    updated = await update_account(session, loan.id, test_workspace.id, AccountUpdate(type="checking"))

    assert updated is not None
    assert updated.type == "checking"
    assert updated.loan_subtype is None
    assert updated.secured_asset_id is None
    assert updated.loan_details is None


@pytest.mark.asyncio
async def test_connected_account_can_become_a_loan(session, test_user, test_workspace):
    conn = await _connection(session, test_user)
    acct = await _account(
        session, test_user, name="Synced loan", acc_type="checking", balance="-300000", connection_id=conn.id
    )
    asset = await _asset(session, test_user, test_workspace.id)

    updated = await update_account(
        session,
        acct.id,
        test_workspace.id,
        AccountUpdate(type="loan", loan_subtype="mortgage", secured_asset_id=asset.id),
    )

    assert updated is not None
    assert updated.type == "loan"
    assert updated.loan_subtype == "mortgage"
    assert updated.secured_asset_id == asset.id
    # The provider balance is not rewritten: only credit cards are negated.
    assert updated.balance == Decimal("-300000.00")


@pytest.mark.asyncio
async def test_connected_loan_still_refuses_other_fields(session, test_user, test_workspace):
    conn = await _connection(session, test_user)
    acct = await _account(
        session, test_user, name="Synced loan", acc_type="loan", balance="-1000", connection_id=conn.id
    )

    with pytest.raises(ValueError, match="Cannot edit bank-connected accounts"):
        await update_account(session, acct.id, test_workspace.id, AccountUpdate(name="Renamed"))
    with pytest.raises(ValueError, match="Credit card fields"):
        await update_account(
            session, acct.id, test_workspace.id, AccountUpdate(credit_limit=Decimal("10"))
        )


@pytest.mark.asyncio
async def test_deleting_the_asset_unlinks_its_loans(session, test_user, test_workspace):
    asset = await _asset(session, test_user, test_workspace.id)
    first = await _manual_loan(session, test_workspace, test_user, "1000")
    second = await _manual_loan(session, test_workspace, test_user, "2000")
    for loan in (first, second):
        await update_account(session, loan.id, test_workspace.id, AccountUpdate(secured_asset_id=asset.id))

    assert await asset_service.delete_asset(session, asset.id, test_workspace.id) is True

    for loan in (first, second):
        reread = await account_service.get_account(session, loan.id, test_workspace.id)
        assert reread is not None
        await session.refresh(reread)
        assert reread.secured_asset_id is None
        assert reread.type == "loan"


def test_reconciliation_rules_accept_loan_accounts():
    assert set(_validate_account_types(["loan", "checking"])) == {"checking", "loan"}


# ---------------------------------------------------------------------------
# net worth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_net_worth_unchanged_by_flipping_a_negative_account_to_loan(
    session, test_user, test_workspace
):
    checking = await create_account(
        session, test_workspace.id, test_user.id,
        AccountCreate(name="Everyday", type="checking", balance=Decimal("10000"), currency="BRL"),
    )
    manual_debt = await create_account(
        session, test_workspace.id, test_user.id,
        AccountCreate(name="Old loan", type="checking", balance=Decimal("-300000"), currency="BRL"),
    )
    conn = await _connection(session, test_user)
    synced_debt = await _account(
        session, test_user, name="Synced loan", acc_type="checking", balance="-50000", connection_id=conn.id
    )
    today = app_today()

    before = await _net_worth_at(session, test_workspace.id, today, primary_currency="BRL")
    for acct in (manual_debt, synced_debt):
        await update_account(session, acct.id, test_workspace.id, AccountUpdate(type="loan"))
    after = await _net_worth_at(session, test_workspace.id, today, primary_currency="BRL")

    assert before.value == pytest.approx(10000 - 300000 - 50000)
    assert after.value == pytest.approx(before.value)
    assert after.breakdowns == before.breakdowns
    assert await _balance(session, test_workspace, manual_debt.id) == pytest.approx(-300000.0)
    assert await _balance(session, test_workspace, checking.id) == pytest.approx(10000.0)
