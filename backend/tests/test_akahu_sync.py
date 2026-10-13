"""Akahu through the real sync pipeline.

One Akahu connection reaches several banks. Every account it brings must be
an ordinary individual account, exactly like another provider's: its own
institution, its own collection membership, and the user's own display name,
type and closed state, all kept across syncs. Investment, KiwiSaver and
wallet accounts are accounts too (type ``investment``), and net worth counts
them once, not again through their holdings.

Akahu is faked with ``httpx.MockTransport``; every name and figure is made up.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.services.crypto import encrypt
from app.core.app_clock import app_today
from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.asset_value import AssetValue
from app.models.bank_connection import BankConnection
from app.models.collection import Collection
from app.models.goal import Goal
from app.models.institution import Institution
from app.models.transaction import Transaction
from app.providers.akahu import AkahuProvider
from app.schemas.account import AccountUpdate
from app.schemas.bank_connection import BankConnectionRead
from app.schemas.collection import CollectionCreate
from app.services import account_service, collection_service
from app.services.connection_service import get_connections, sync_connection
from app.services.dashboard_service import get_summary
from app.services.goal_service import _resolve_current_amount
from app.services.report_service import get_net_worth_report

USER = "user_token_test123"
APP = "app_token_test789"


def _bank(conn_id: str, name: str, **over) -> dict:
    return {"_id": conn_id, "name": name, "logo": f"https://logo.example/{conn_id}.png", **over}


def _raw(_id: str, name: str, akahu_type: str, connection: dict, current: float, **over) -> dict:
    raw = {
        "_id": _id,
        "name": name,
        "type": akahu_type,
        "status": "ACTIVE",
        "attributes": ["TRANSACTIONS"],
        "balance": {"currency": "NZD", "current": current},
        "connection": connection,
    }
    raw.update(over)
    return raw


BANK_A = _bank("conn_a", "Example Bank")
BANK_B = _bank("conn_b", "Second Bank", connection_type="official")
BANK_C = _bank("conn_c", "Card Company", connection_type="classic")


def bank_accounts() -> list[dict]:
    """Three banks behind one Akahu login."""
    return [
        _raw("acc_chq", "Everyday", "CHECKING", BANK_A, 1500.0,
             formatted_account="01-0001-0000001-00"),
        _raw("acc_sav", "Rainy Day", "SAVINGS", BANK_A, 8000.0,
             formatted_account="01-0001-0000001-01"),
        _raw("acc_visa", "Visa", "CREDITCARD", BANK_B, 300.0,
             balance={"currency": "NZD", "current": 300.0, "limit": 5000}),
        _raw("acc_card", "Platinum Card", "CREDITCARD", BANK_C, -120.0),
    ]


def fake_akahu(accounts: list[dict]):
    """Patch AkahuProvider's HTTP client to answer from ``accounts``."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/accounts"):
            return httpx.Response(200, json={"success": True, "items": accounts})
        if path.endswith("/transactions"):
            return httpx.Response(200, json={"success": True, "items": [], "cursor": {"next": None}})
        if path.endswith("/me"):
            return httpx.Response(200, json={"success": True, "item": {"_id": "user_1"}})
        return httpx.Response(404, json={"success": False})

    transport = httpx.MockTransport(handler)

    async def fake_client(self, credentials=None):  # noqa: ANN001
        return httpx.AsyncClient(transport=transport, timeout=30)

    return patch.object(AkahuProvider, "_client", fake_client)


async def akahu_connection(session: AsyncSession, user, workspace) -> BankConnection:
    conn = BankConnection(
        user_id=user.id,
        workspace_id=workspace.id,
        provider="akahu",
        external_id="akahu-user_1",
        institution_name="Akahu",
        credentials={"user_token_enc": encrypt(USER), "app_token_enc": encrypt(APP)},
        status="active",
    )
    session.add(conn)
    await session.commit()
    return conn


async def run_sync(session: AsyncSession, conn: BankConnection, workspace, user,
                   accounts: list[dict], provider: AkahuProvider | None = None) -> None:
    provider = provider or AkahuProvider()
    with fake_akahu(accounts), \
         patch("app.services.connection_service.get_provider", return_value=provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock):
        await sync_connection(session, conn.id, workspace.id, user.id)


async def accounts_by_external_id(session: AsyncSession, conn: BankConnection) -> dict[str, Account]:
    rows = await session.execute(
        select(Account).where(Account.connection_id == conn.id).execution_options(populate_existing=True)
    )
    return {a.external_id: a for a in rows.scalars().all() if a.external_id}


async def connection_read(session: AsyncSession, workspace, conn: BankConnection) -> BankConnectionRead:
    for c in await get_connections(session, workspace.id):
        if c.id == conn.id:
            await session.refresh(c, attribute_names=["institutions"])
            return BankConnectionRead.model_validate(c)
    raise AssertionError("connection not listed")


# ----- Part A: one Akahu login, individual accounts ----------------------------


@pytest.mark.asyncio
async def test_each_account_carries_its_own_institution(session, test_user, test_workspace):
    conn = await akahu_connection(session, test_user, test_workspace)
    await run_sync(session, conn, test_workspace, test_user, bank_accounts())

    rows = await accounts_by_external_id(session, conn)
    assert set(rows) == {"acc_chq", "acc_sav", "acc_visa", "acc_card"}
    institutions = {
        i.id: i
        for i in (
            await session.execute(select(Institution).where(Institution.connection_id == conn.id))
        ).scalars()
    }
    by_account = {ext: institutions[a.institution_id].name for ext, a in rows.items()}
    assert by_account == {
        "acc_chq": "Example Bank",
        "acc_sav": "Example Bank",
        "acc_visa": "Second Bank",
        "acc_card": "Card Company",
    }

    read = await connection_read(session, test_workspace, conn)
    assert sorted(i.name for i in read.institutions) == ["Card Company", "Example Bank", "Second Bank"]

    # Every account is listed on its own, under its own bank's name and logo.
    listed = {
        a["external_id"]: a
        for a in await account_service.get_accounts(session, test_workspace.id)
        if a["connection_id"] == conn.id
    }
    assert listed["acc_visa"]["institution_name"] == "Second Bank"
    assert listed["acc_visa"]["institution_logo_url"] == "https://logo.example/conn_b.png"
    assert listed["acc_chq"]["institution_name"] == "Example Bank"
    # The account keeps the name the bank gives it; no institution label is
    # written over it.
    assert listed["acc_sav"]["display_name"] is None
    assert listed["acc_sav"]["name"] == "Rainy Day"


@pytest.mark.asyncio
async def test_one_account_of_the_login_joins_a_collection_on_its_own(session, test_user, test_workspace):
    conn = await akahu_connection(session, test_user, test_workspace)
    await run_sync(session, conn, test_workspace, test_user, bank_accounts())
    rows = await accounts_by_external_id(session, conn)

    created = await collection_service.create_collection(
        session, test_workspace.id, test_user.id,
        CollectionCreate(name="Day to day", account_ids=[rows["acc_chq"].id, rows["acc_visa"].id]),
    )
    assert set(created.account_ids) == {rows["acc_chq"].id, rows["acc_visa"].id}

    await run_sync(session, conn, test_workspace, test_user, bank_accounts())
    collection = await session.get(Collection, created.id, populate_existing=True)
    assert collection is not None
    assert {a.external_id for a in collection.accounts} == {"acc_chq", "acc_visa"}


@pytest.mark.asyncio
async def test_the_users_name_type_and_closed_state_survive_a_sync(session, test_user, test_workspace):
    conn = await akahu_connection(session, test_user, test_workspace)
    await run_sync(session, conn, test_workspace, test_user, bank_accounts())
    rows = await accounts_by_external_id(session, conn)

    await account_service.update_account(
        session, rows["acc_sav"].id, test_workspace.id,
        AccountUpdate(display_name="Holiday fund", type="checking"),
    )
    await account_service.close_account(session, rows["acc_card"].id, test_workspace.id)

    changed = bank_accounts()
    changed[1]["balance"] = {"currency": "NZD", "current": 9000.0}
    changed[3]["balance"] = {"currency": "NZD", "current": -999.0}
    await run_sync(session, conn, test_workspace, test_user, changed)

    rows = await accounts_by_external_id(session, conn)
    assert len(rows) == 4  # no duplicate rows minted for edited accounts
    assert rows["acc_sav"].display_name == "Holiday fund"
    assert rows["acc_sav"].type == "checking"
    assert rows["acc_sav"].balance == Decimal("9000")
    assert rows["acc_card"].is_closed is True
    assert rows["acc_card"].balance == Decimal("120")  # closed: left as it was


# ----- Part B: investment, KiwiSaver and wallet accounts are accounts ---------

BROKER = _bank("conn_d", "Share Broker")
FUNDS = _bank("conn_e", "Fund Manager")


def five_bank_accounts() -> list[dict]:
    """The three banks plus a share broker and a fund manager.

    The broker reports its uninvested cash on both the brokerage account and
    its wallet (as Hatch does): the brokerage balance is positions plus cash.
    """
    return bank_accounts() + [
        _raw("acc_broker", "Share account", "INVESTMENT", BROKER, 2300.0, attributes=[],
             meta={"portfolio": [
                 {"symbol": "EXA", "fund_id": "f1", "name": "Example Corp",
                  "shares": 10, "value": 1500.0, "currency": "NZD", "returns": 200.0},
                 {"symbol": "SMP", "fund_id": "f2", "name": "Sample Ltd",
                  "shares": 4, "value": 400.0, "currency": "NZD"},
             ]}),
        _raw("acc_broker_wallet", "Share account wallet", "WALLET", BROKER, 400.0, attributes=[]),
        _raw("acc_ks", "KiwiSaver Growth", "KIWISAVER", FUNDS, 25000.0, attributes=["TRANSACTIONS"],
             meta={"portfolio": [{"name": "Growth Fund", "value": 25000.0}]}),
        _raw("acc_fund", "Balanced Fund", "INVESTMENT", FUNDS, 7000.0, attributes=[]),
    ]


INVESTMENT_IDS = {"acc_broker", "acc_broker_wallet", "acc_ks", "acc_fund"}
HOLDING_IDS = {"acc_broker::EXA", "acc_broker::SMP", "acc_broker_wallet", "acc_ks", "acc_fund"}
# Banks 1500 + 8000 - 300 - 120; investments 1900 + 400 + 25000 + 7000.
NET_WORTH = Decimal("43380")


class LegacyAkahu(AkahuProvider):
    """Akahu as the fork synced it before this change: investment, KiwiSaver
    and wallet accounts arrived only as holdings, never as accounts."""

    @property
    def holdings_in_account_balance(self) -> bool:
        return False

    async def get_accounts(self, credentials):  # noqa: ANN001
        return [a for a in await super().get_accounts(credentials) if a.type != "investment"]


SALARY = {
    "_id": "trans_salary", "_account": "acc_chq", "date": "2026-10-01T01:00:00.000Z",
    "description": "SALARY", "amount": 3000.0, "type": "CREDIT",
}


def fake_akahu_with_salary(accounts: list[dict]):
    """fake_akahu, plus one salary row on the cheque account so P&L is not empty."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/accounts"):
            return httpx.Response(200, json={"success": True, "items": accounts})
        if path.endswith("/acc_chq/transactions"):
            return httpx.Response(200, json={"success": True, "items": [SALARY], "cursor": {"next": None}})
        if path.endswith("/transactions"):
            return httpx.Response(200, json={"success": True, "items": [], "cursor": {"next": None}})
        return httpx.Response(404, json={"success": False})

    transport = httpx.MockTransport(handler)

    async def fake_client(self, credentials=None):  # noqa: ANN001
        return httpx.AsyncClient(transport=transport, timeout=30)

    return patch.object(AkahuProvider, "_client", fake_client)


async def sync_salaried(session, conn, workspace, user, provider: AkahuProvider) -> None:
    with fake_akahu_with_salary(five_bank_accounts()), \
         patch("app.services.connection_service.get_provider", return_value=provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock):
        await sync_connection(session, conn.id, workspace.id, user.id)


async def nzd_user(session: AsyncSession, user) -> None:
    user.preferences = {**(user.preferences or {}), "currency_display": "NZD"}
    await session.commit()


async def money_view(session: AsyncSession, workspace, user, **collection) -> dict:
    """Net worth (dashboard, report, net-worth goal) and P&L as the user sees them."""
    summary = await get_summary(session, workspace.id, user.id, month=date(2026, 10, 1), **collection)
    report = await get_net_worth_report(
        session, workspace.id, user.id, months=1, interval="daily", **collection
    )
    view = {
        "dashboard": round(Decimal(str(summary.total_balance_primary)), 2),
        "report": round(Decimal(str(report.summary.primary_value)), 2),
        # Every past day too: the history must not change.
        "history": [(p.date, round(p.value, 2)) for p in report.trend],
        "income": summary.monthly_income,
        "expenses": summary.monthly_expenses,
    }
    if not collection:
        goal = Goal(user_id=user.id, workspace_id=workspace.id, name="Everything",
                    target_amount=Decimal("1000000"), currency="NZD", tracking_type="net_worth")
        view["goal"] = round(await _resolve_current_amount(session, goal, user.id), 2)
    return view


async def connection_assets(session: AsyncSession, conn: BankConnection) -> dict[str, Asset]:
    rows = await session.execute(
        select(Asset).where(Asset.connection_id == conn.id).execution_options(populate_existing=True)
    )
    return {a.external_id: a for a in rows.scalars().all() if a.external_id}


@pytest.mark.asyncio
async def test_a_fresh_connection_counts_investments_once(session, test_user, test_workspace):
    await nzd_user(session, test_user)
    conn = await akahu_connection(session, test_user, test_workspace)
    await run_sync(session, conn, test_workspace, test_user, five_bank_accounts())

    rows = await accounts_by_external_id(session, conn)
    assert {ext for ext, a in rows.items() if a.type == "investment"} == INVESTMENT_IDS
    assert rows["acc_broker"].balance == Decimal("1900")  # its wallet carries the 400 cash
    assert rows["acc_ks"].balance == Decimal("25000")

    read = await connection_read(session, test_workspace, conn)
    assert sorted(i.name for i in read.institutions) == [
        "Card Company", "Example Bank", "Fund Manager", "Second Bank", "Share Broker",
    ]
    listed = {
        a["external_id"]: a
        for a in await account_service.get_accounts(session, test_workspace.id)
        if a["connection_id"] == conn.id
    }
    assert listed["acc_ks"]["type"] == "investment"
    assert listed["acc_ks"]["institution_name"] == "Fund Manager"
    assert listed["acc_broker_wallet"]["institution_name"] == "Share Broker"

    # Holdings are kept as detail, linked to their account, in a wallet backed
    # by the account's own institution.
    assets = await connection_assets(session, conn)
    assert set(assets) == HOLDING_IDS
    assert {a.account_external_id for a in assets.values()} == INVESTMENT_IDS
    wallet = await session.get(AssetGroup, assets["acc_ks"].group_id)
    assert wallet is not None and wallet.institution_id == rows["acc_ks"].institution_id

    view = await money_view(session, test_workspace, test_user)
    assert view["dashboard"] == view["report"] == view["goal"] == NET_WORTH


@pytest.mark.asyncio
async def test_switching_an_existing_connection_keeps_net_worth_and_history(
    session, test_user, test_workspace
):
    """Investments synced as holdings only; the next sync makes them accounts.
    Net worth, P&L, the holdings, their value history and a collection's
    totals stay as they were, and no money is counted twice."""
    await nzd_user(session, test_user)
    conn = await akahu_connection(session, test_user, test_workspace)
    await sync_salaried(session, conn, test_workspace, test_user, LegacyAkahu())

    assets_before = await connection_assets(session, conn)
    assert set(assets_before) == HOLDING_IDS
    rows = await accounts_by_external_id(session, conn)
    assert not INVESTMENT_IDS & set(rows)
    # An older value: the holding's history must survive the switch.
    session.add(AssetValue(asset_id=assets_before["acc_ks"].id, amount=Decimal("24000"),
                           date=app_today() - timedelta(days=3), source="sync"))
    await session.commit()
    ks_wallet = assets_before["acc_ks"].group_id
    assert ks_wallet is not None
    collection = await collection_service.create_collection(
        session, test_workspace.id, test_user.id,
        CollectionCreate(name="Retirement", account_ids=[rows["acc_chq"].id], wallet_ids=[ks_wallet]),
    )
    before = await money_view(session, test_workspace, test_user)
    before_collection = await money_view(
        session, test_workspace, test_user,
        account_ids=[rows["acc_chq"].id], asset_group_ids=[ks_wallet],
    )
    assert before["dashboard"] == before["report"] == before["goal"] == NET_WORTH
    assert before["income"] > 0

    await sync_salaried(session, conn, test_workspace, test_user, AkahuProvider())

    rows = await accounts_by_external_id(session, conn)
    assert {ext for ext, a in rows.items() if a.type == "investment"} == INVESTMENT_IDS
    assets_after = await connection_assets(session, conn)
    assert {k: a.id for k, a in assets_after.items()} == {k: a.id for k, a in assets_before.items()}
    assert not any(a.is_archived for a in assets_after.values())
    assert {a.account_external_id for a in assets_after.values()} == INVESTMENT_IDS
    history = (
        await session.execute(
            select(AssetValue.amount).where(AssetValue.asset_id == assets_after["acc_ks"].id)
        )
    ).scalars().all()
    assert Decimal("24000") in history
    # The account's own balance history is its holdings' value history.
    chart = await account_service.get_account_balance_history(
        session, rows["acc_ks"].id, test_workspace.id,
        date_from=app_today() - timedelta(days=4), date_to=app_today(),
    )
    assert chart is not None
    assert [p["balance"] for p in chart] == [0.0, 24000.0, 24000.0, 24000.0, 25000.0]
    # No transactions on the investment accounts (only the synthetic opening
    # balance every connected account gets), so nothing reaches P&L.
    on_investments = await session.scalar(
        select(func.count()).select_from(Transaction).where(
            Transaction.account_id.in_([rows[e].id for e in INVESTMENT_IDS]),
            Transaction.source != "opening_balance",
        )
    )
    assert on_investments == 0

    assert await money_view(session, test_workspace, test_user) == before
    # The collection that held the KiwiSaver wallet now holds its account too,
    # so its totals don't change either.
    stored = await session.get(Collection, collection.id, populate_existing=True)
    assert stored is not None
    assert {a.external_id for a in stored.accounts} == {"acc_chq", "acc_ks"}
    after_collection = await money_view(
        session, test_workspace, test_user,
        account_ids=[a.id for a in stored.accounts], asset_group_ids=[ks_wallet],
    )
    assert after_collection == before_collection

    # A further sync changes nothing.
    await sync_salaried(session, conn, test_workspace, test_user, AkahuProvider())
    assert set(await accounts_by_external_id(session, conn)) == set(rows)
    assert await money_view(session, test_workspace, test_user) == before


@pytest.mark.asyncio
async def test_a_closed_investment_account_hands_its_value_back_to_its_holdings(
    session, test_user, test_workspace
):
    await nzd_user(session, test_user)
    conn = await akahu_connection(session, test_user, test_workspace)
    await run_sync(session, conn, test_workspace, test_user, five_bank_accounts())
    rows = await accounts_by_external_id(session, conn)

    await account_service.close_account(session, rows["acc_ks"].id, test_workspace.id)
    view = await money_view(session, test_workspace, test_user)
    assert view["dashboard"] == view["report"] == view["goal"] == NET_WORTH
