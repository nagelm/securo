"""Equity of an asset against the loans secured on it (display only).

An asset's equity is its current value minus the debt of the open loan
accounts whose secured_asset_id points at it. It is never added to any total:
net worth already counts the loan as a liability and the asset at its value.
Every amount here is made up.
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_value import AssetValue
from app.models.bank_connection import BankConnection
from app.models.fx_rate import FxRate
from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.account import AccountCreate, AccountUpdate
from app.services import asset_service
from app.services.account_service import create_account, update_account
from app.services.report_service import _net_worth_at


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _asset(
    session: AsyncSession,
    user: User,
    workspace: Workspace,
    *,
    value: str | None = "500000",
    currency: str = "BRL",
    asset_type: str = "real_estate",
    name: str = "Test house",
) -> Asset:
    asset = Asset(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        name=name,
        type=asset_type,
        currency=currency,
    )
    session.add(asset)
    await session.flush()
    if value is not None:
        session.add(
            AssetValue(
                id=uuid.uuid4(),
                asset_id=asset.id,
                workspace_id=workspace.id,
                amount=Decimal(value),
                date=app_today() - timedelta(days=10),
                source="manual",
            )
        )
    await session.commit()
    await session.refresh(asset)
    return asset


async def _loan(
    session: AsyncSession,
    workspace: Workspace,
    user: User,
    owed: str,
    *,
    asset: Asset | None,
    name: str = "Test mortgage",
    currency: str = "BRL",
) -> Account:
    loan = await create_account(
        session,
        workspace.id,
        user.id,
        AccountCreate(
            name=name,
            type="loan",
            balance=Decimal(owed),
            currency=currency,
            balance_date=app_today() - timedelta(days=40),
        ),
    )
    if asset is not None:
        await update_account(
            session, loan.id, workspace.id, AccountUpdate(secured_asset_id=asset.id)
        )
    return loan


async def _read(session: AsyncSession, workspace: Workspace, asset: Asset):
    read = await asset_service.get_asset(session, asset.id, workspace.id)
    assert read is not None
    return read


# ---------------------------------------------------------------------------
# equity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_loan_house_500k_mortgage_300k(session, test_user, test_workspace):
    house = await _asset(session, test_user, test_workspace)
    loan = await _loan(session, test_workspace, test_user, "300000", asset=house)

    read = await _read(session, test_workspace, house)

    assert read.current_value == pytest.approx(500000.0)
    assert read.linked_loan_ids == [loan.id]
    assert read.linked_debt == pytest.approx(300000.0)
    assert read.equity == pytest.approx(200000.0)
    [linked] = read.linked_loans
    assert linked.id == loan.id
    assert linked.name == "Test mortgage"
    assert linked.balance == pytest.approx(-300000.0)
    assert linked.debt == pytest.approx(300000.0)


@pytest.mark.asyncio
async def test_two_loans_on_one_asset_are_summed(session, test_user, test_workspace):
    house = await _asset(session, test_user, test_workspace)
    first = await _loan(session, test_workspace, test_user, "250000", asset=house, name="Loan A")
    second = await _loan(session, test_workspace, test_user, "50000", asset=house, name="Loan B")

    read = await _read(session, test_workspace, house)

    assert read.linked_loan_ids == [first.id, second.id]
    assert read.linked_debt == pytest.approx(300000.0)
    assert read.equity == pytest.approx(200000.0)


@pytest.mark.asyncio
async def test_overpaid_loan_counts_as_no_debt(session, test_user, test_workspace):
    house = await _asset(session, test_user, test_workspace)
    loan = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Test overpaid loan",
        type="loan",
        balance=Decimal("0"),
        currency="BRL",
        secured_asset_id=house.id,
    )
    session.add(loan)
    await session.commit()
    # A manual account's balance is its signed rows; a credit leaves it positive.
    from app.models.transaction import Transaction

    session.add(
        Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            account_id=loan.id,
            description="overpayment",
            amount=Decimal("120.00"),
            currency="BRL",
            date=app_today() - timedelta(days=1),
            type="credit",
            source="manual",
            status="posted",
            created_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()

    read = await _read(session, test_workspace, house)

    assert read.linked_loan_ids == [loan.id]
    assert read.linked_debt == pytest.approx(0.0)
    assert read.equity == pytest.approx(500000.0)


@pytest.mark.asyncio
async def test_connected_loan_uses_the_provider_balance(session, test_user, test_workspace):
    house = await _asset(session, test_user, test_workspace)
    conn = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="test",
        external_id=f"ext-{uuid.uuid4().hex[:8]}",
        institution_name="Test Bank",
        credentials={},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.flush()
    session.add(
        Account(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            connection_id=conn.id,
            name="Test synced mortgage",
            type="loan",
            balance=Decimal("-280000"),
            currency="BRL",
            secured_asset_id=house.id,
        )
    )
    await session.commit()

    read = await _read(session, test_workspace, house)

    assert read.linked_debt == pytest.approx(280000.0)
    assert read.equity == pytest.approx(220000.0)


@pytest.mark.asyncio
async def test_cross_currency_loan_is_converted_to_the_asset_currency(
    session, test_user, test_workspace
):
    # 1 USD = 5 BRL: a 20,000 USD loan is 100,000 BRL of debt on a BRL house.
    session.add(
        FxRate(
            id=uuid.uuid4(),
            base_currency="USD",
            quote_currency="BRL",
            date=app_today(),
            rate=Decimal("5.0"),
            source="test",
        )
    )
    await session.commit()
    house = await _asset(session, test_user, test_workspace)
    await _loan(session, test_workspace, test_user, "20000", asset=house, currency="USD")

    read = await _read(session, test_workspace, house)

    [linked] = read.linked_loans
    assert linked.currency == "USD"
    assert linked.balance == pytest.approx(-20000.0)
    assert linked.debt == pytest.approx(100000.0)
    assert read.linked_debt == pytest.approx(100000.0)
    assert read.equity == pytest.approx(400000.0)


@pytest.mark.asyncio
async def test_asset_without_a_value_has_debt_but_no_equity(session, test_user, test_workspace):
    land = await _asset(session, test_user, test_workspace, value=None, name="Test land")
    await _loan(session, test_workspace, test_user, "300000", asset=land)

    read = await _read(session, test_workspace, land)

    assert read.current_value is None
    assert read.linked_debt == pytest.approx(300000.0)
    assert read.equity is None


@pytest.mark.asyncio
async def test_archived_or_sold_asset_shows_no_equity(session, test_user, test_workspace):
    archived = await _asset(session, test_user, test_workspace, name="Test archived")
    sold = await _asset(session, test_user, test_workspace, name="Test sold")
    archived.is_archived = True
    sold.sell_date = app_today() - timedelta(days=5)
    sold.sell_price = Decimal("510000")
    await session.commit()
    await _loan(session, test_workspace, test_user, "300000", asset=archived)
    await _loan(session, test_workspace, test_user, "300000", asset=sold)

    for asset in (archived, sold):
        read = await _read(session, test_workspace, asset)
        assert read.linked_debt == pytest.approx(300000.0)
        assert read.equity is None


@pytest.mark.asyncio
async def test_unlinked_asset_has_no_loan_fields(session, test_user, test_workspace):
    house = await _asset(session, test_user, test_workspace)
    await _loan(session, test_workspace, test_user, "300000", asset=None)

    read = await _read(session, test_workspace, house)

    assert read.linked_loan_ids == []
    assert read.linked_loans == []
    assert read.linked_debt is None
    assert read.equity is None


@pytest.mark.asyncio
async def test_closed_loan_is_not_counted(session, test_user, test_workspace):
    house = await _asset(session, test_user, test_workspace)
    loan = await _loan(session, test_workspace, test_user, "300000", asset=house)
    loan.is_closed = True
    await session.commit()

    read = await _read(session, test_workspace, house)

    assert read.linked_loan_ids == []
    assert read.equity is None


@pytest.mark.asyncio
async def test_list_and_update_carry_equity(session, test_user, test_workspace):
    house = await _asset(session, test_user, test_workspace)
    car = await _asset(
        session, test_user, test_workspace, value="30000", asset_type="vehicle", name="Test car"
    )
    await _loan(session, test_workspace, test_user, "300000", asset=house)
    await _loan(session, test_workspace, test_user, "12000", asset=car, name="Test car loan")

    reads = {r.id: r for r in await asset_service.get_assets(session, test_workspace.id)}
    assert reads[house.id].equity == pytest.approx(200000.0)
    assert reads[car.id].equity == pytest.approx(18000.0)

    from app.schemas.asset import AssetUpdate

    updated = await asset_service.update_asset(
        session, house.id, test_workspace.id, test_user.id, AssetUpdate(name="Test home")
    )
    assert updated is not None
    assert updated.equity == pytest.approx(200000.0)


@pytest.mark.asyncio
async def test_equity_is_not_added_to_net_worth(session, test_user, test_workspace):
    house = await _asset(session, test_user, test_workspace)
    loan = await _loan(session, test_workspace, test_user, "300000", asset=None)
    today = app_today()

    before = await _net_worth_at(session, test_workspace.id, today, "BRL")
    await update_account(
        session, loan.id, test_workspace.id, AccountUpdate(secured_asset_id=house.id)
    )
    after = await _net_worth_at(session, test_workspace.id, today, "BRL")

    assert before.value == pytest.approx(200000.0)
    assert after.value == pytest.approx(before.value)
    assert after.breakdowns == before.breakdowns
    read = await _read(session, test_workspace, house)
    assert read.equity == pytest.approx(after.value)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_api_returns_equity_and_primary_fields(
    client: AsyncClient, auth_headers, session, test_user, test_workspace
):
    # The test user's primary currency is BRL; 1 USD = 5 BRL.
    session.add(
        FxRate(
            id=uuid.uuid4(),
            base_currency="USD",
            quote_currency="BRL",
            date=app_today(),
            rate=Decimal("5.0"),
            source="test",
        )
    )
    await session.commit()
    house = await _asset(session, test_user, test_workspace)
    flat = await _asset(
        session, test_user, test_workspace, value="100000", currency="USD", name="Test flat"
    )
    loan = await _loan(session, test_workspace, test_user, "300000", asset=house)
    await _loan(
        session, test_workspace, test_user, "60000", asset=flat, name="Test flat loan",
        currency="USD",
    )

    response = await client.get("/api/assets", headers=auth_headers)

    assert response.status_code == 200, response.text
    by_id = {a["id"]: a for a in response.json()}
    home = by_id[str(house.id)]
    assert home["linked_loan_ids"] == [str(loan.id)]
    assert home["linked_debt"] == pytest.approx(300000.0)
    assert home["equity"] == pytest.approx(200000.0)
    # Same currency as the user's: primary fields stay null, like current_value_primary.
    assert home["equity_primary"] is None
    assert home["linked_loans"][0]["name"] == "Test mortgage"

    usd = by_id[str(flat.id)]
    assert usd["linked_debt"] == pytest.approx(60000.0)
    assert usd["equity"] == pytest.approx(40000.0)
    assert usd["linked_debt_primary"] == pytest.approx(300000.0)
    assert usd["equity_primary"] == pytest.approx(200000.0)

    single = await client.get(f"/api/assets/{house.id}", headers=auth_headers)
    assert single.status_code == 200
    assert single.json()["equity"] == pytest.approx(200000.0)


@pytest.mark.asyncio
async def test_portfolio_trend_carries_secured_debt_per_date(session, test_user, test_workspace):
    """Fork: each trend row says what the loans on an asset owed that day, so
    the chart can show equity; the gross values are unchanged."""
    from app.models.transaction import Transaction

    house = await _asset(session, test_user, test_workspace)
    loan = Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Test mortgage", type="loan", balance=Decimal("0"), currency="BRL",
        secured_asset_id=house.id,
    )
    session.add(loan)
    await session.commit()
    today = app_today()
    for days_ago, kind, amount in ((5, "debit", "300000"), (1, "credit", "1000")):
        session.add(Transaction(
            id=uuid.uuid4(), user_id=test_user.id, account_id=loan.id,
            description=kind, amount=Decimal(amount), currency="BRL",
            date=today - timedelta(days=days_ago), type=kind, source="manual",
            status="posted", created_at=datetime.now(timezone.utc),
        ))
    session.add(AssetValue(id=uuid.uuid4(), asset_id=house.id, amount=Decimal("510000"),
                           date=today - timedelta(days=3)))
    session.add(AssetValue(id=uuid.uuid4(), asset_id=house.id, amount=Decimal("520000"),
                           date=today))
    await session.commit()

    result = await asset_service.get_portfolio_trend(session, test_workspace.id, test_user.id)
    rows = {r["date"]: r for r in result["trend"]}
    aid = str(house.id)
    mid = rows[(today - timedelta(days=3)).isoformat()]
    assert mid[f"debt:{aid}"] == pytest.approx(300000.0)
    assert mid[aid] == pytest.approx(510000.0)
    last = rows[today.isoformat()]
    assert last[f"debt:{aid}"] == pytest.approx(299000.0)
    assert result["debt_total"] == pytest.approx(299000.0)
    assert result["total"] == pytest.approx(520000.0)
