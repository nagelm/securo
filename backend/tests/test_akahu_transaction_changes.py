"""Akahu bank edits and removals through the real sync pipeline.

Akahu keeps a settled transaction's ``_id`` when the bank later changes its
date, text or amount; removes it when the bank removes it; and, for a row
changed beyond recognition, deletes it and creates a new one under a new id
(developers.akahu.nz, "Keeping transactions synchronised"). The sync applies
the bank's fields to the stored row, never the user's; deletes a removed
row unless that would lose something the user made (then it is kept and
tagged); and folds a re-issued row into one.

Akahu is faked with ``httpx.MockTransport``; every name and figure is made up.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.services.crypto import encrypt
from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.category import Category
from app.models.group import Group, GroupMember
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.models.transaction_attachment import TransactionAttachment
from app.models.transaction_split import TransactionSplit
from app.providers.akahu import AkahuProvider
from app.services.connection_service import sync_connection
from app.services.provider_transaction_changes import REMOVED_MARKER, REMOVED_TAG

pytestmark = pytest.mark.asyncio

USER = "user_token_test123"
APP = "app_token_test789"
NZ_TODAY = datetime.now(ZoneInfo("Pacific/Auckland")).date()


def day(offset: int) -> date:
    return NZ_TODAY - timedelta(days=offset)


def akahu_row(_id: str, offset: int, amount: float, description: str = "EXAMPLE STORE") -> dict:
    return {
        "_id": _id,
        "_account": "acc_chq",
        # 01:00 UTC is the afternoon of the same New Zealand day.
        "date": f"{day(offset).isoformat()}T01:00:00.000Z",
        "description": description,
        "amount": amount,
        "type": "EFTPOS",
    }


class FakeAkahu:
    """One cheque account whose transaction feed a test edits between syncs."""

    def __init__(self, rows: list[dict]):
        self.rows = list(rows)
        self.status = "ACTIVE"
        self.page_size: int | None = None
        self.fail_second_page = False

    def account(self) -> dict:
        return {
            "_id": "acc_chq",
            "name": "Everyday",
            "type": "CHECKING",
            "status": self.status,
            "attributes": ["TRANSACTIONS"],
            "balance": {"currency": "NZD", "current": 1000.0},
            "connection": {"_id": "conn_a", "name": "Example Bank"},
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/accounts"):
            return httpx.Response(200, json={"success": True, "items": [self.account()]})
        if path.endswith("/transactions"):
            if self.page_size is None:
                return httpx.Response(
                    200, json={"success": True, "items": self.rows, "cursor": {"next": None}}
                )
            if request.url.params.get("cursor"):
                if self.fail_second_page:
                    return httpx.Response(502, json={"success": False})
                return httpx.Response(
                    200,
                    json={"success": True, "items": self.rows[self.page_size:], "cursor": {"next": None}},
                )
            return httpx.Response(
                200,
                json={"success": True, "items": self.rows[: self.page_size], "cursor": {"next": "page2"}},
            )
        return httpx.Response(404, json={"success": False})


class WithoutChanges(AkahuProvider):
    """The same feed from a provider that doesn't claim in-place edits or removals."""

    @property
    def edits_transactions_in_place(self) -> bool:
        return False

    @property
    def reports_removed_transactions(self) -> bool:
        return False


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


async def run_sync(session, conn, workspace, user, fake: FakeAkahu, provider=None) -> None:
    transport = httpx.MockTransport(fake.handler)

    async def fake_client(self, credentials=None):  # noqa: ANN001
        return httpx.AsyncClient(transport=transport, timeout=30)

    with patch.object(AkahuProvider, "_client", fake_client), \
         patch("app.services.connection_service.get_provider",
               return_value=provider or AkahuProvider()), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.provider_transaction_changes.stamp_primary_amount",
               new_callable=AsyncMock):
        await sync_connection(session, conn.id, workspace.id, user.id)


async def synced(session: AsyncSession, conn: BankConnection) -> dict[str, Transaction]:
    rows = await session.execute(
        select(Transaction)
        .join(Account, Account.id == Transaction.account_id)
        .where(Account.connection_id == conn.id, Transaction.source != "opening_balance")
        .execution_options(populate_existing=True)
    )
    return {t.external_id: t for t in rows.scalars().all() if t.external_id}


async def category(session: AsyncSession, workspace, name: str) -> Category:
    cat = Category(
        id=uuid.uuid4(), user_id=workspace.created_by_user_id, workspace_id=workspace.id,
        name=name, icon="tag", color="#000000",
    )
    session.add(cat)
    await session.commit()
    return cat


async def attach(session: AsyncSession, tx: Transaction) -> None:
    session.add(TransactionAttachment(
        transaction_id=tx.id, user_id=tx.user_id, workspace_id=tx.workspace_id,
        filename="receipt.pdf", storage_key=f"test/{tx.id}.pdf",
        content_type="application/pdf", size=10,
    ))
    await session.commit()


# ----- edits -----------------------------------------------------------------


async def test_a_bank_edit_updates_the_row_in_place_and_keeps_the_users_fields(
    session, test_user, test_workspace
):
    fake = FakeAkahu([akahu_row("trans_1", 10, -12.5, "EXAMPLE STORE")])
    conn = await akahu_connection(session, test_user, test_workspace)
    await run_sync(session, conn, test_workspace, test_user, fake)

    groceries = await category(session, test_workspace, "Groceries")
    tx = (await synced(session, conn))["trans_1"]
    tx_id = tx.id
    tx.category_id = groceries.id
    tx.notes = "Shared with flatmate #house"
    tx.exclude_from_pnl = True
    await session.commit()

    fake.rows = [akahu_row("trans_1", 9, -13.75, "EXAMPLE STORE AUCKLAND")]
    await run_sync(session, conn, test_workspace, test_user, fake)

    rows = await synced(session, conn)
    assert list(rows) == ["trans_1"]
    tx = rows["trans_1"]
    assert tx.id == tx_id  # updated in place, not re-inserted
    assert tx.date == day(9)
    assert tx.effective_date == day(9)
    assert tx.amount == Decimal("13.75")
    assert tx.type == "debit"
    assert tx.original_description == "Example Store Auckland"
    # The description was still the bank's text, so it follows the bank.
    assert tx.description == "Example Store Auckland"
    assert tx.raw_data is not None and tx.raw_data["amount"] == -13.75
    # The user's fields are untouched.
    assert tx.category_id == groceries.id
    assert tx.notes == "Shared with flatmate #house"
    assert tx.exclude_from_pnl is True


async def test_a_users_or_rules_description_survives_a_bank_text_change(
    session, test_user, test_workspace
):
    fake = FakeAkahu([
        akahu_row("trans_user", 10, -20.0, "POS W/D 1234"),
        akahu_row("trans_rule", 11, -30.0, "DD INSURER"),
    ])
    conn = await akahu_connection(session, test_user, test_workspace)
    await run_sync(session, conn, test_workspace, test_user, fake)

    rows = await synced(session, conn)
    rows["trans_user"].description = "Coffee with a friend"
    rows["trans_rule"].description = "Home insurance"
    rows["trans_rule"].description_is_rule_managed = True
    await session.commit()

    fake.rows = [
        akahu_row("trans_user", 10, -20.0, "POS W/D 1234 CAFE"),
        akahu_row("trans_rule", 11, -30.0, "DD INSURER LTD"),
    ]
    await run_sync(session, conn, test_workspace, test_user, fake)

    rows = await synced(session, conn)
    assert rows["trans_user"].description == "Coffee with a friend"
    assert rows["trans_user"].original_description == "Pos W/D 1234 Cafe"
    assert rows["trans_rule"].description == "Home insurance"
    assert rows["trans_rule"].description_is_rule_managed is True
    assert rows["trans_rule"].original_description == "Dd Insurer Ltd"


def with_category(row: dict, name: str) -> dict:
    return {**row, "category": {"_id": "nzfcc_test", "name": name}}


async def test_a_category_the_provider_adds_later_reaches_an_uncategorized_row(
    session, test_user, test_workspace
):
    fake = FakeAkahu([
        akahu_row("trans_late", 10, -8.5, "EXAMPLE CAFE"),
        akahu_row("trans_mine", 11, -9.0, "OTHER CAFE"),
    ])
    conn = await akahu_connection(session, test_user, test_workspace)
    await run_sync(session, conn, test_workspace, test_user, fake)

    eating_out = await category(session, test_workspace, "Eating out")
    eating_out.default_key = "food"
    leisure = await category(session, test_workspace, "Leisure")
    rows = await synced(session, conn)
    assert rows["trans_late"].category_id is None
    rows["trans_mine"].category_id = leisure.id
    await session.commit()

    fake.rows = [
        with_category(akahu_row("trans_late", 10, -8.5, "EXAMPLE CAFE"), "Cafes and restaurants"),
        with_category(akahu_row("trans_mine", 11, -9.0, "OTHER CAFE"), "Cafes and restaurants"),
    ]
    await run_sync(session, conn, test_workspace, test_user, fake)

    rows = await synced(session, conn)
    assert rows["trans_late"].category_id == eating_out.id
    # A category the user chose is never replaced.
    assert rows["trans_mine"].category_id == leisure.id


async def test_a_rule_category_beats_the_late_provider_category(session, test_user, test_workspace):
    fake = FakeAkahu([akahu_row("trans_late", 10, -8.5, "EXAMPLE BAKERY")])
    conn = await akahu_connection(session, test_user, test_workspace)
    await run_sync(session, conn, test_workspace, test_user, fake)

    eating_out = await category(session, test_workspace, "Eating out")
    eating_out.default_key = "food"
    treats = await category(session, test_workspace, "Treats")
    session.add(Rule(
        user_id=test_user.id, workspace_id=test_workspace.id, name="Bakeries are treats",
        conditions=[{"field": "description", "op": "contains", "value": "BAKERY"}],
        actions=[{"op": "set_category", "value": str(treats.id)}], priority=5,
    ))
    await session.commit()

    fake.rows = [with_category(akahu_row("trans_late", 10, -8.5, "EXAMPLE BAKERY"), "Cafes and restaurants")]
    await run_sync(session, conn, test_workspace, test_user, fake)

    assert (await synced(session, conn))["trans_late"].category_id == treats.id


async def test_an_amount_edit_moves_a_mirrored_transfer_leg_and_rescales_equal_splits(
    session, test_user, test_workspace
):
    fake = FakeAkahu([
        akahu_row("trans_xfer", 10, -100.0, "TO SAVINGS"),
        akahu_row("trans_split", 12, -60.0, "DINNER"),
    ])
    conn = await akahu_connection(session, test_user, test_workspace)
    await run_sync(session, conn, test_workspace, test_user, fake)
    rows = await synced(session, conn)

    cash = Account(
        user_id=test_user.id, workspace_id=test_workspace.id, name="Cash jar",
        type="checking", balance=Decimal("0"), currency="NZD",
    )
    session.add(cash)
    await session.flush()
    pair_id = uuid.uuid4()
    rows["trans_xfer"].transfer_pair_id = pair_id
    mirror = Transaction(
        user_id=test_user.id, workspace_id=test_workspace.id, account_id=cash.id,
        description="To savings", amount=Decimal("100"), currency="NZD", date=day(10),
        type="credit", source="transfer", transfer_pair_id=pair_id,
    )
    session.add(mirror)
    group = Group(user_id=test_user.id, workspace_id=test_workspace.id, name="Flat")
    session.add(group)
    await session.flush()
    members = [
        GroupMember(group_id=group.id, workspace_id=test_workspace.id, name=name)
        for name in ("Me", "Flatmate")
    ]
    session.add_all(members)
    await session.flush()
    for member in members:
        session.add(TransactionSplit(
            transaction_id=rows["trans_split"].id, workspace_id=test_workspace.id,
            group_member_id=member.id, share_amount=Decimal("30"), share_type="equal",
        ))
    await session.commit()

    fake.rows = [
        akahu_row("trans_xfer", 9, -120.0, "TO SAVINGS"),
        akahu_row("trans_split", 12, -61.01, "DINNER"),
    ]
    await run_sync(session, conn, test_workspace, test_user, fake)

    await session.refresh(mirror)
    assert mirror.amount == Decimal("120")
    assert mirror.date == day(9)
    assert mirror.transfer_pair_id == pair_id
    shares = (
        await session.execute(
            select(TransactionSplit.share_amount)
            .where(TransactionSplit.transaction_id == rows["trans_split"].id)
            .execution_options(populate_existing=True)
        )
    ).scalars().all()
    assert sorted(shares) == [Decimal("30.50"), Decimal("30.51")]


# ----- removals --------------------------------------------------------------


async def test_a_row_the_bank_removed_is_deleted(session, test_user, test_workspace):
    fake = FakeAkahu([
        akahu_row("trans_keep", 10, -12.5),
        akahu_row("trans_gone", 11, -40.0, "DUPLICATE CHARGE"),
    ])
    conn = await akahu_connection(session, test_user, test_workspace)
    await run_sync(session, conn, test_workspace, test_user, fake)
    assert set(await synced(session, conn)) == {"trans_keep", "trans_gone"}

    fake.rows = [akahu_row("trans_keep", 10, -12.5)]
    await run_sync(session, conn, test_workspace, test_user, fake)

    assert set(await synced(session, conn)) == {"trans_keep"}
    # The opening balance absorbs nothing for the removed row: the stored
    # rows still add up to the provider's balance.
    account = (await session.execute(select(Account).where(Account.connection_id == conn.id))).scalar_one()
    stored = (
        await session.execute(
            select(Transaction).where(Transaction.account_id == account.id)
            .execution_options(populate_existing=True)
        )
    ).scalars().all()
    total = sum(
        (t.amount if t.type == "credit" else -t.amount for t in stored), Decimal("0")
    )
    assert total == Decimal("1000")


async def test_a_removed_row_with_an_attachment_is_kept_and_tagged(session, test_user, test_workspace):
    fake = FakeAkahu([
        akahu_row("trans_keep", 10, -12.5),
        akahu_row("trans_receipt", 11, -40.0, "HARDWARE STORE"),
    ])
    conn = await akahu_connection(session, test_user, test_workspace)
    await run_sync(session, conn, test_workspace, test_user, fake)
    receipt = (await synced(session, conn))["trans_receipt"]
    receipt.notes = "Warranty claim"
    await session.commit()
    await attach(session, receipt)

    fake.rows = [akahu_row("trans_keep", 10, -12.5)]
    await run_sync(session, conn, test_workspace, test_user, fake)

    rows = await synced(session, conn)
    kept = rows["trans_receipt"]
    assert kept.notes == f"Warranty claim {REMOVED_TAG}"
    assert kept.raw_data is not None and REMOVED_MARKER in kept.raw_data
    # A later sync that still doesn't see it changes nothing more.
    await run_sync(session, conn, test_workspace, test_user, fake)
    assert (await synced(session, conn))["trans_receipt"].notes == f"Warranty claim {REMOVED_TAG}"

    # The bank puts it back: the flag goes.
    fake.rows = [akahu_row("trans_keep", 10, -12.5), akahu_row("trans_receipt", 11, -40.0, "HARDWARE STORE")]
    await run_sync(session, conn, test_workspace, test_user, fake)
    back = (await synced(session, conn))["trans_receipt"]
    assert back.notes == "Warranty claim"
    assert back.raw_data is not None and REMOVED_MARKER not in back.raw_data


async def test_a_removed_transfer_leg_is_kept_and_tagged(session, test_user, test_workspace):
    fake = FakeAkahu([akahu_row("trans_keep", 10, -12.5), akahu_row("trans_leg", 11, -50.0)])
    conn = await akahu_connection(session, test_user, test_workspace)
    await run_sync(session, conn, test_workspace, test_user, fake)
    leg = (await synced(session, conn))["trans_leg"]
    leg.transfer_pair_id = uuid.uuid4()
    await session.commit()

    fake.rows = [akahu_row("trans_keep", 10, -12.5)]
    await run_sync(session, conn, test_workspace, test_user, fake)

    leg = (await synced(session, conn))["trans_leg"]
    assert leg.transfer_pair_id is not None
    assert leg.notes == REMOVED_TAG


async def test_a_partial_read_removes_nothing(session, test_user, test_workspace):
    fake = FakeAkahu([akahu_row(f"trans_{n}", 5 + n, -10.0 - n) for n in range(4)])
    conn = await akahu_connection(session, test_user, test_workspace)
    await run_sync(session, conn, test_workspace, test_user, fake)

    # Page one arrives, page two fails: the sync fails and nothing is removed.
    fake.rows = fake.rows[:1] + fake.rows[2:]  # trans_1 dropped
    fake.page_size = 1
    fake.fail_second_page = True
    with pytest.raises(RuntimeError):
        await run_sync(session, conn, test_workspace, test_user, fake)
    assert set(await synced(session, conn)) == {f"trans_{n}" for n in range(4)}


async def test_an_inactive_account_removes_nothing(session, test_user, test_workspace):
    fake = FakeAkahu([akahu_row("trans_a", 10, -12.5), akahu_row("trans_b", 11, -40.0)])
    conn = await akahu_connection(session, test_user, test_workspace)
    await run_sync(session, conn, test_workspace, test_user, fake)

    fake.status = "INACTIVE"
    fake.rows = [akahu_row("trans_a", 10, -12.5)]
    await run_sync(session, conn, test_workspace, test_user, fake)
    assert set(await synced(session, conn)) == {"trans_a", "trans_b"}


async def test_a_mass_drop_or_an_empty_read_removes_nothing(session, test_user, test_workspace):
    fake = FakeAkahu([akahu_row(f"trans_{n}", 5 + n, -10.0 - n) for n in range(10)])
    conn = await akahu_connection(session, test_user, test_workspace)
    await run_sync(session, conn, test_workspace, test_user, fake)
    every = {f"trans_{n}" for n in range(10)}

    fake.rows = fake.rows[:2]  # eight of ten gone at once
    await run_sync(session, conn, test_workspace, test_user, fake)
    assert set(await synced(session, conn)) == every

    fake.rows = []
    await run_sync(session, conn, test_workspace, test_user, fake)
    assert set(await synced(session, conn)) == every


async def test_rows_near_the_window_edge_are_left_alone(session, test_user, test_workspace):
    """A row dated at the start of the re-read window may have moved just
    outside it; its absence proves nothing."""
    fake = FakeAkahu([akahu_row("trans_new", 5, -12.5), akahu_row("trans_edge", 29, -40.0)])
    conn = await akahu_connection(session, test_user, test_workspace)
    await run_sync(session, conn, test_workspace, test_user, fake)

    fake.rows = [akahu_row("trans_new", 5, -12.5)]
    await run_sync(session, conn, test_workspace, test_user, fake)
    assert set(await synced(session, conn)) == {"trans_new", "trans_edge"}


async def test_a_provider_without_the_capabilities_is_unaffected(session, test_user, test_workspace):
    fake = FakeAkahu([akahu_row("trans_a", 10, -12.5), akahu_row("trans_b", 11, -40.0)])
    conn = await akahu_connection(session, test_user, test_workspace)
    await run_sync(session, conn, test_workspace, test_user, fake, provider=WithoutChanges())

    fake.rows = [akahu_row("trans_a", 9, -99.0, "CHANGED")]
    await run_sync(session, conn, test_workspace, test_user, fake, provider=WithoutChanges())

    rows = await synced(session, conn)
    assert set(rows) == {"trans_a", "trans_b"}
    assert rows["trans_a"].amount == Decimal("12.5")
    assert rows["trans_a"].date == day(10)
    assert rows["trans_a"].description == "Example Store"


# ----- re-issued rows --------------------------------------------------------


async def test_a_reissued_row_ends_as_one_row_with_the_users_data(session, test_user, test_workspace):
    fake = FakeAkahu([akahu_row("trans_keep", 10, -12.5), akahu_row("trans_old", 12, -75.0, "POS 4471 GARAGE")])
    conn = await akahu_connection(session, test_user, test_workspace)
    await run_sync(session, conn, test_workspace, test_user, fake)

    car = await category(session, test_workspace, "Car")
    old = (await synced(session, conn))["trans_old"]
    old.category_id = car.id
    old.notes = "WOF and service"
    old.description = "Car service"
    await session.commit()
    await attach(session, old)

    # The bank changed the row beyond recognition; Akahu re-issued it.
    fake.rows = [akahu_row("trans_keep", 10, -12.5), akahu_row("trans_new", 11, -75.0, "GARAGE LTD")]
    await run_sync(session, conn, test_workspace, test_user, fake)

    rows = await synced(session, conn)
    assert set(rows) == {"trans_keep", "trans_new"}
    new = rows["trans_new"]
    assert new.date == day(11)
    assert new.category_id == car.id
    assert new.notes == "WOF and service"
    assert new.description == "Car service"
    assert new.original_description == "Garage Ltd"
    attachments = (
        await session.execute(
            select(TransactionAttachment.transaction_id)
            .where(TransactionAttachment.workspace_id == test_workspace.id)
            .execution_options(populate_existing=True)
        )
    ).scalars().all()
    assert attachments == [new.id]


async def test_an_ambiguous_reissue_carries_nothing_over(session, test_user, test_workspace):
    """Two removed rows of the same charge and one new one: which was re-issued
    can't be told, so the new row keeps its own fields."""
    fake = FakeAkahu([
        akahu_row("trans_keep", 8, -1.0),
        akahu_row("trans_old_a", 10, -5.0, "BUS"),
        akahu_row("trans_old_b", 11, -5.0, "BUS"),
    ])
    conn = await akahu_connection(session, test_user, test_workspace)
    await run_sync(session, conn, test_workspace, test_user, fake)
    transport = await category(session, test_workspace, "Bus fares")
    for tx in (await synced(session, conn)).values():
        if tx.external_id != "trans_keep":
            tx.category_id = transport.id
    await session.commit()

    fake.rows = [akahu_row("trans_keep", 8, -1.0), akahu_row("trans_new", 10, -5.0, "BUS")]
    await run_sync(session, conn, test_workspace, test_user, fake)

    rows = await synced(session, conn)
    assert set(rows) == {"trans_keep", "trans_new"}
    assert rows["trans_new"].category_id != transport.id


# ----- accounts new to an existing connection --------------------------------


class RecordingAkahu(AkahuProvider):
    """Akahu that notes the ``since`` each account was read from."""

    def __init__(self, seen: dict[str, date | None]):
        self.seen = seen

    async def get_transactions(self, credentials, account_external_id, since=None, payee_source="auto"):  # noqa: ANN001
        self.seen[account_external_id] = since
        return await super().get_transactions(credentials, account_external_id, since, payee_source)


class TwoAccountAkahu(FakeAkahu):
    """FakeAkahu plus a card the bank starts reporting later."""

    def __init__(self, rows: list[dict]):
        super().__init__(rows)
        self.card_rows: list[dict] | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if self.card_rows is not None:
            if path.endswith("/accounts"):
                card = {**self.account(), "_id": "acc_card", "name": "Shared card",
                        "balance": {"currency": "NZD", "current": 0.0}}
                return httpx.Response(200, json={"success": True, "items": [self.account(), card]})
            if path.endswith("/acc_card/transactions"):
                return httpx.Response(
                    200, json={"success": True, "items": self.card_rows, "cursor": {"next": None}}
                )
        return super().handler(request)


async def test_an_account_new_to_the_connection_reads_its_full_history(
    session, test_user, test_workspace
):
    fake = TwoAccountAkahu([akahu_row("trans_chq", 10, -12.5)])
    conn = await akahu_connection(session, test_user, test_workspace)
    await run_sync(session, conn, test_workspace, test_user, fake)

    # A card shared later: its first sync must not start from the
    # connection's last sync.
    fake.card_rows = [{**akahu_row("trans_card_old", 200, -80.0), "_account": "acc_card"}]
    seen: dict[str, date | None] = {}
    await run_sync(session, conn, test_workspace, test_user, fake, provider=RecordingAkahu(seen))
    assert seen["acc_card"] is None
    assert seen["acc_chq"] is not None
    assert "trans_card_old" in await synced(session, conn)

    # Once it has synced rows, it reads the recent window like the others.
    seen.clear()
    await run_sync(session, conn, test_workspace, test_user, fake, provider=RecordingAkahu(seen))
    assert seen["acc_card"] is not None
