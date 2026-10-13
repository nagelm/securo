"""Akahu through the real sync pipeline.

One Akahu connection reaches several banks. Every account it brings must be
an ordinary individual account, exactly like another provider's: its own
institution, its own collection membership, and the user's own display name,
type and closed state, all kept across syncs.

Akahu is faked with ``httpx.MockTransport``; every name and figure is made up.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.services.crypto import encrypt
from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.collection import Collection
from app.models.institution import Institution
from app.providers.akahu import AkahuProvider
from app.schemas.account import AccountUpdate
from app.schemas.bank_connection import BankConnectionRead
from app.schemas.collection import CollectionCreate
from app.services import account_service, collection_service
from app.services.connection_service import get_connections, sync_connection

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
