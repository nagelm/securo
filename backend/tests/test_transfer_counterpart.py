"""The other leg's account on each transfer leg (From A → To B pill)."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.transaction import Transaction
from app.models.user import User
from app.services.transaction_calendar_service import get_transaction_calendar
from app.services.transfer_counterpart import attach_transfer_counterpart_accounts


@pytest_asyncio.fixture
async def savings(session: AsyncSession, test_user: User, test_connection: BankConnection) -> Account:
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        connection_id=test_connection.id,
        external_id="acc-ext-savings",
        name="Rainy Day",
        type="savings",
        balance=Decimal("100.00"),
        currency="BRL",
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


async def _transfer(client: AsyncClient, auth_headers, from_id, to_id, amount=50.0, day=None):
    response = await client.post(
        "/api/transactions/transfer",
        json={
            "from_account_id": str(from_id),
            "to_account_id": str(to_id),
            "amount": amount,
            "date": (day or date.today()).isoformat(),
            "description": "Move money",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_list_gives_each_leg_the_other_legs_account(
    client: AsyncClient, auth_headers, test_account: Account, savings: Account
):
    pair = await _transfer(client, auth_headers, test_account.id, savings.id)
    plain = await client.post(
        "/api/transactions",
        json={
            "account_id": str(test_account.id),
            "description": "Coffee",
            "amount": 4.0,
            "date": date.today().isoformat(),
            "type": "debit",
        },
        headers=auth_headers,
    )
    assert plain.status_code == 201

    response = await client.get("/api/transactions", headers=auth_headers)
    assert response.status_code == 200
    by_id = {item["id"]: item for item in response.json()["items"]}

    assert by_id[pair["debit"]["id"]]["transfer_counterpart_account_id"] == str(savings.id)
    assert by_id[pair["credit"]["id"]]["transfer_counterpart_account_id"] == str(test_account.id)
    assert by_id[plain.json()["id"]]["transfer_counterpart_account_id"] is None


@pytest.mark.asyncio
async def test_single_transaction_carries_the_counterpart_account(
    client: AsyncClient, auth_headers, test_account: Account, savings: Account
):
    pair = await _transfer(client, auth_headers, test_account.id, savings.id)

    response = await client.get(f"/api/transactions/{pair['credit']['id']}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["transfer_counterpart_account_id"] == str(test_account.id)


@pytest.mark.asyncio
async def test_one_query_for_a_whole_page(
    session: AsyncSession, client: AsyncClient, auth_headers, test_account: Account, savings: Account
):
    account_id, savings_id = test_account.id, savings.id
    pairs = [await _transfer(client, auth_headers, account_id, savings_id, amount=10 + i) for i in range(4)]
    ids = [uuid.UUID(p[leg]["id"]) for p in pairs for leg in ("debit", "credit")]
    txs: list[Transaction] = []
    for tx_id in ids:
        loaded = await session.get(Transaction, tx_id)
        assert loaded is not None
        txs.append(loaded)

    statements: list[str] = []

    def count(conn, cursor, statement, *args):
        statements.append(statement)

    sync_engine = session.bind.sync_engine  # type: ignore[union-attr]
    event.listen(sync_engine, "before_cursor_execute", count)
    try:
        await attach_transfer_counterpart_accounts(session, txs)
    finally:
        event.remove(sync_engine, "before_cursor_execute", count)

    assert len(statements) == 1
    for p in pairs:
        debit = next(t for t in txs if str(t.id) == p["debit"]["id"])
        credit = next(t for t in txs if str(t.id) == p["credit"]["id"])
        assert debit.transfer_counterpart_account_id == savings_id
        assert credit.transfer_counterpart_account_id == account_id


@pytest.mark.asyncio
async def test_no_query_when_nothing_is_paired(session: AsyncSession, test_user: User, test_account: Account):
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        account_id=test_account.id,
        description="Lunch",
        amount=Decimal("12"),
        currency="BRL",
        date=date.today(),
        type="debit",
        source="manual",
    )
    statements: list[str] = []

    def count(conn, cursor, statement, *args):
        statements.append(statement)

    sync_engine = session.bind.sync_engine  # type: ignore[union-attr]
    event.listen(sync_engine, "before_cursor_execute", count)
    try:
        await attach_transfer_counterpart_accounts(session, [tx])
    finally:
        event.remove(sync_engine, "before_cursor_execute", count)

    assert statements == []
    assert tx.transfer_counterpart_account_id is None


@pytest.mark.asyncio
async def test_a_leg_whose_partner_is_gone_gets_none(
    session: AsyncSession, test_user: User, test_account: Account
):
    orphan = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        account_id=test_account.id,
        description="Half a transfer",
        amount=Decimal("30"),
        currency="BRL",
        date=date.today(),
        type="debit",
        source="transfer",
        transfer_pair_id=uuid.uuid4(),
    )
    session.add(orphan)
    await session.commit()

    await attach_transfer_counterpart_accounts(session, [orphan])

    assert orphan.transfer_counterpart_account_id is None


@pytest.mark.asyncio
async def test_calendar_items_carry_the_counterpart_account(
    session: AsyncSession, client: AsyncClient, auth_headers, test_user: User,
    test_workspace, test_account: Account, savings: Account,
):
    day = date(2026, 7, 8)
    pair = await _transfer(client, auth_headers, test_account.id, savings.id, day=day)

    calendar = await get_transaction_calendar(session, test_workspace.id, test_user.id, month=date(2026, 7, 1))

    items = {str(i.id): i for d in calendar.days if d.date == day for i in d.items}
    assert items[pair["debit"]["id"]].transfer_counterpart_account_id == savings.id
    assert items[pair["credit"]["id"]].transfer_counterpart_account_id == test_account.id
