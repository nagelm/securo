"""Akahu pending transactions through the real sync pipeline (fork).

Akahu's pending rows have no id and change before they settle, so each read
replaces them: a pending row lives while Akahu lists it, then gives way to its
settled row, which keeps whatever the user changed on the pending one.

Akahu is faked with ``httpx.MockTransport``; names and figures are made up.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.services.crypto import encrypt
from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.category import Category
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.providers.akahu import AkahuProvider
from app.services.connection_service import sync_connection

BANK = {"_id": "conn_a", "name": "Example Bank", "logo": "https://logo.example/a.png"}
CARD = {
    "_id": "acc_card",
    "name": "Card",
    "type": "CREDITCARD",
    "status": "ACTIVE",
    "attributes": ["TRANSACTIONS"],
    "balance": {"currency": "NZD", "current": -100.0, "limit": 5000},
    "connection": BANK,
}
SETTLED_OLD = {
    "_id": "trans_old",
    "_account": "acc_card",
    "date": "2026-10-01T11:00:00.000Z",
    "description": "OLD SHOP",
    "amount": -100.0,
    "type": "DEBIT",
}
PENDING = {
    "_account": "acc_card",
    "date": "2026-10-13T22:04:17.000Z",
    "description": "EXAMPLE FASHION OUTLET",
    "amount": -99.9,
    "type": "DEBIT",
    "meta": {},
}
SETTLED_NEW = {
    "_id": "trans_new",
    "_account": "acc_card",
    "date": "2026-10-14T11:00:00.000Z",
    "description": "Example Fashion",
    "amount": -99.9,
    "type": "DEBIT",
}


class Bank:
    """What the fake Akahu answers; tests change it between syncs."""

    def __init__(self) -> None:
        self.settled = [SETTLED_OLD]
        self.pending: list[dict] | None = [PENDING]
        self.balance = -100.0

    def patch(self):
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/accounts"):
                card = {**CARD, "balance": {"currency": "NZD", "current": self.balance, "limit": 5000}}
                return httpx.Response(200, json={"success": True, "items": [card]})
            if path.endswith("/transactions/pending"):
                if self.pending is None:
                    return httpx.Response(500, json={"success": False})
                return httpx.Response(200, json={"success": True, "items": self.pending})
            if path.endswith("/transactions"):
                return httpx.Response(
                    200, json={"success": True, "items": self.settled, "cursor": {"next": None}}
                )
            return httpx.Response(404, json={"success": False})

        transport = httpx.MockTransport(handler)

        async def fake_client(self_, credentials=None):  # noqa: ANN001
            return httpx.AsyncClient(transport=transport, timeout=30)

        return patch.object(AkahuProvider, "_client", fake_client)


async def _connection(session: AsyncSession, user, workspace) -> BankConnection:
    conn = BankConnection(
        user_id=user.id,
        workspace_id=workspace.id,
        provider="akahu",
        external_id="akahu-user_1",
        institution_name="Akahu",
        credentials={"user_token_enc": encrypt("user_token_x"), "app_token_enc": encrypt("app_token_y")},
        status="active",
    )
    session.add(conn)
    await session.commit()
    return conn


async def _sync(session: AsyncSession, conn: BankConnection, workspace, user, bank: Bank) -> None:
    with bank.patch(), \
         patch("app.services.connection_service.get_provider", return_value=AkahuProvider()), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock):
        await sync_connection(session, conn.id, workspace.id, user.id)


async def _rows(session: AsyncSession) -> list[Transaction]:
    account = (await session.execute(select(Account).where(Account.external_id == "acc_card"))).scalar_one()
    rows = await session.execute(
        select(Transaction)
        .where(Transaction.account_id == account.id, Transaction.source != "opening_balance")
        .order_by(Transaction.date)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars().all())


async def _opening(session: AsyncSession) -> Decimal:
    account = (await session.execute(select(Account).where(Account.external_id == "acc_card"))).scalar_one()
    row = (
        await session.execute(
            select(Transaction).where(
                Transaction.account_id == account.id, Transaction.source == "opening_balance"
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return Decimal("0")
    return row.amount if row.type == "credit" else -row.amount


@pytest.mark.asyncio
async def test_a_pending_purchase_shows_as_pending(session, test_user, test_workspace):
    bank = Bank()
    conn = await _connection(session, test_user, test_workspace)
    await _sync(session, conn, test_workspace, test_user, bank)

    rows = await _rows(session)
    assert [(r.status, r.amount, r.date) for r in rows] == [
        ("posted", Decimal("100.00"), date(2026, 10, 2)),
        ("pending", Decimal("99.90"), date(2026, 10, 14)),
    ]
    assert (rows[1].external_id or "").startswith("pending:")


@pytest.mark.asyncio
async def test_the_opening_balance_does_not_absorb_pending_rows(session, test_user, test_workspace):
    """Akahu's balance leaves pending rows out, so neither must the opening row."""
    bank = Bank()
    conn = await _connection(session, test_user, test_workspace)
    await _sync(session, conn, test_workspace, test_user, bank)
    with_pending = await _opening(session)

    bank.pending = []
    await _sync(session, conn, test_workspace, test_user, bank)
    assert await _opening(session) == with_pending


@pytest.mark.asyncio
async def test_a_pending_row_still_listed_stays_one_row(session, test_user, test_workspace):
    bank = Bank()
    conn = await _connection(session, test_user, test_workspace)
    await _sync(session, conn, test_workspace, test_user, bank)
    await _sync(session, conn, test_workspace, test_user, bank)

    assert [r.status for r in await _rows(session)] == ["posted", "pending"]


@pytest.mark.asyncio
async def test_the_settled_row_replaces_the_pending_one(session, test_user, test_workspace):
    bank = Bank()
    conn = await _connection(session, test_user, test_workspace)
    await _sync(session, conn, test_workspace, test_user, bank)

    bank.pending = []
    bank.settled = [SETTLED_OLD, SETTLED_NEW]
    bank.balance = -211.9
    await _sync(session, conn, test_workspace, test_user, bank)

    rows = await _rows(session)
    assert [(r.status, r.external_id, r.description) for r in rows] == [
        ("posted", "trans_old", "Old Shop"),
        ("posted", "trans_new", "Example Fashion"),
    ]


@pytest.mark.asyncio
async def test_the_users_edits_move_to_the_settled_row(session, test_user, test_workspace):
    bank = Bank()
    conn = await _connection(session, test_user, test_workspace)
    await _sync(session, conn, test_workspace, test_user, bank)

    clothes = Category(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Clothes", icon="shirt", color="#000000",
    )
    session.add(clothes)
    pending = (await _rows(session))[1]
    pending.category_id = clothes.id
    pending.notes = "Birthday jacket"
    await session.commit()

    bank.pending = []
    bank.settled = [SETTLED_OLD, SETTLED_NEW]
    await _sync(session, conn, test_workspace, test_user, bank)

    settled = (await _rows(session))[1]
    assert (settled.external_id, settled.category_id, settled.notes) == (
        "trans_new", clothes.id, "Birthday jacket",
    )


@pytest.mark.asyncio
async def test_a_pending_charge_takes_its_recurring_placeholder_and_settles_linked(
    session, test_user, test_workspace
):
    bank = Bank()
    bank.pending = []
    conn = await _connection(session, test_user, test_workspace)
    await _sync(session, conn, test_workspace, test_user, bank)

    account = (await session.execute(select(Account).where(Account.external_id == "acc_card"))).scalar_one()
    clothes = Category(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Clothes", icon="shirt", color="#000000",
    )
    session.add(clothes)
    due = date(2026, 10, 14)
    recurring = RecurringTransaction(
        user_id=test_user.id, workspace_id=test_workspace.id, account_id=account.id,
        description="Fashion club", amount=Decimal("99.90"), currency="NZD", type="debit",
        frequency="monthly", start_date=due, next_occurrence=date(2026, 11, 14),
        category_id=clothes.id,
    )
    session.add(recurring)
    await session.flush()
    session.add(Transaction(
        user_id=test_user.id, workspace_id=test_workspace.id, account_id=account.id,
        recurring_transaction_id=recurring.id, description="Fashion club", amount=Decimal("99.90"),
        currency="NZD", date=due, effective_date=due, type="debit", source="recurring",
        status="pending", category_id=clothes.id,
    ))
    await session.commit()

    bank.pending = [PENDING]
    await _sync(session, conn, test_workspace, test_user, bank)
    rows = await _rows(session)
    assert [(r.source, r.status, r.recurring_transaction_id) for r in rows][1:] == [
        ("sync", "pending", recurring.id),
    ]
    assert len(rows) == 2

    bank.pending = []
    bank.settled = [SETTLED_OLD, SETTLED_NEW]
    await _sync(session, conn, test_workspace, test_user, bank)
    rows = await _rows(session)
    assert [(r.external_id, r.recurring_transaction_id, r.category_id) for r in rows][1:] == [
        ("trans_new", recurring.id, clothes.id),
    ]
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_a_dropped_charge_linked_to_a_recurring_item_waits_for_its_settled_row(
    session, test_user, test_workspace
):
    bank = Bank()
    conn = await _connection(session, test_user, test_workspace)
    await _sync(session, conn, test_workspace, test_user, bank)
    account = (await session.execute(select(Account).where(Account.external_id == "acc_card"))).scalar_one()
    recurring = RecurringTransaction(
        user_id=test_user.id, workspace_id=test_workspace.id, account_id=account.id,
        description="Fashion club", amount=Decimal("99.90"), currency="NZD", type="debit",
        frequency="monthly", start_date=date(2026, 10, 14), next_occurrence=date(2026, 11, 14),
    )
    session.add(recurring)
    await session.flush()
    (await _rows(session))[1].recurring_transaction_id = recurring.id
    await session.commit()

    bank.pending = []
    with patch("app.services.pending_rows.app_today", return_value=date(2026, 10, 15)):
        await _sync(session, conn, test_workspace, test_user, bank)
    assert [r.status for r in await _rows(session)] == ["posted", "pending"]


@pytest.mark.asyncio
async def test_a_dropped_authorisation_goes(session, test_user, test_workspace):
    """Gone from the pending list with nothing settled: the bank dropped it."""
    bank = Bank()
    conn = await _connection(session, test_user, test_workspace)
    await _sync(session, conn, test_workspace, test_user, bank)

    bank.pending = []
    await _sync(session, conn, test_workspace, test_user, bank)

    assert [r.external_id for r in await _rows(session)] == ["trans_old"]


@pytest.mark.asyncio
async def test_an_edited_pending_row_waits_for_its_settled_row(session, test_user, test_workspace):
    """Akahu may drop it a read before the settled copy shows; the edit must survive."""
    bank = Bank()
    conn = await _connection(session, test_user, test_workspace)
    await _sync(session, conn, test_workspace, test_user, bank)
    pending = (await _rows(session))[1]
    pending.notes = "keep me"
    await session.commit()

    bank.pending = []
    with patch("app.services.pending_rows.app_today", return_value=date(2026, 10, 15)):
        await _sync(session, conn, test_workspace, test_user, bank)
    assert [r.status for r in await _rows(session)] == ["posted", "pending"]

    bank.settled = [SETTLED_OLD, SETTLED_NEW]
    with patch("app.services.pending_rows.app_today", return_value=date(2026, 10, 16)):
        await _sync(session, conn, test_workspace, test_user, bank)
    rows = await _rows(session)
    assert [(r.external_id, r.notes) for r in rows][1] == ("trans_new", "keep me")
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_two_identical_pending_rows_are_two_rows(session, test_user, test_workspace):
    bank = Bank()
    bank.pending = [PENDING, PENDING]
    conn = await _connection(session, test_user, test_workspace)
    await _sync(session, conn, test_workspace, test_user, bank)
    await _sync(session, conn, test_workspace, test_user, bank)

    assert [r.status for r in await _rows(session)] == ["posted", "pending", "pending"]


@pytest.mark.asyncio
async def test_a_failed_pending_read_keeps_the_pending_rows(session, test_user, test_workspace):
    bank = Bank()
    conn = await _connection(session, test_user, test_workspace)
    await _sync(session, conn, test_workspace, test_user, bank)

    bank.pending = None  # the fake answers 500
    await _sync(session, conn, test_workspace, test_user, bank)

    assert [r.status for r in await _rows(session)] == ["posted", "pending"]


@pytest.mark.asyncio
async def test_a_balance_that_takes_pending_in_keeps_the_opening_row(session, test_user, test_workspace):
    """Some banks (some cheque accounts) take a pending debit straight
    into the balance; the opening row must stay put then too."""
    bank = Bank()
    bank.pending = []
    conn = await _connection(session, test_user, test_workspace)
    await _sync(session, conn, test_workspace, test_user, bank)
    before = await _opening(session)

    bank.pending = [PENDING]
    bank.balance = -100.0 - 99.9
    await _sync(session, conn, test_workspace, test_user, bank)
    assert await _opening(session) == before
